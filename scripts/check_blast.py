#!/usr/bin/env python3
"""Probe NCBI GenBank remote BLAST and write a downdetector-style status.

Two independent signals, because outages show up at different stages:

  SUBMIT health -- a short burst of BLAST URL-API "Put" requests that mirror a
    real `blastn -remote` submission (core_nt + an entrez_query filter). This
    is where the reported failure lived ("Could not queue request: DB Put
    Request error: sp_NewRequestEx failed"). Sampling a burst, and counting
    *any* rejection, catches intermittent queue failures that a single
    retried query would hide.

  RETRIEVAL health -- one real end-to-end `blastn -remote` search, to confirm
    a queued job actually completes and returns results, plus its latency.

Two supporting probes locate the fault when something breaks:
  - neutral connectivity (example.com) -> is *our* network alive?
  - NCBI eutils (einfo)                -> is NCBI (not just BLAST) alive?

Results go to docs/status.json (latest) and a rolling docs/history.json.
"""

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
STATUS_FILE = DOCS / "status.json"
HISTORY_FILE = DOCS / "history.json"
FALLBACK_QUERY = ROOT / "scripts" / "query.fasta"

# Pinned RefSeq used as the query; fetched fresh so retrieval yields hits.
QUERY_ACC = "NM_002046"  # human GAPDH mRNA, one of the most-sequenced genes
DB = "core_nt"           # NCBI's current large nucleotide db (what users hit)
ENTREZ_QUERY = "mitochondrion[Location]"  # mirrors the reported failing command

NEUTRAL_URL = "https://example.com"
EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi"
EFETCH_URL = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    f"?db=nuccore&id={QUERY_ACC}&rettype=fasta&retmode=text"
)
BLAST_CGI = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"

USER_AGENT = "genbank-blast-status/2.0 (github actions monitor)"
HTTP_TIMEOUT = 30          # seconds for lightweight GETs
SUBMIT_TIMEOUT = 60        # seconds per URL-API Put
BLAST_TIMEOUT = 300        # seconds hard cap on the end-to-end blastn
SLOW_THRESHOLD = 180       # retrieval latency above this (s) => SLOW
BURST_N = 3                # submissions sampled per check
BURST_SPACING = 10         # seconds between submissions (NCBI-polite)
HISTORY_LIMIT = 336        # ~7 days at one check / 30 min

# Signals that a Put was rejected at the queue/DB stage rather than accepted.
QUEUE_ERROR_RE = re.compile(
    r"bad_request|sp_NewRequestEx|DB Put Request|Could not queue|"
    r"queue request|ERR:-?\d+|Message ID#", re.IGNORECASE)


def http_ok(url, timeout=HTTP_TIMEOUT):
    """Return True if the URL returns HTTP 200 with some body."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200 and bool(resp.read(1))
    except Exception:
        return False


def fetch_query_seq():
    """Return (sequence_str, source). Fetch pinned query; fall back to file."""
    req = urllib.request.Request(EFETCH_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = resp.read().decode("utf-8", "replace")
        seq = _seq_from_fasta(data)
        if seq:
            return seq, "efetch"
    except Exception:
        pass
    try:
        return _seq_from_fasta(FALLBACK_QUERY.read_text()), "fallback"
    except Exception:
        return "", "none"


def _seq_from_fasta(text):
    return "".join(
        ln.strip() for ln in text.splitlines() if ln and not ln.startswith(">"))


def submit_once(seq):
    """Submit one Put mirroring the real request. Return dict(ok, rid, reason)."""
    params = {
        "CMD": "Put", "PROGRAM": "blastn", "MEGABLAST": "on",
        "DATABASE": DB, "QUERY": seq, "ENTREZ_QUERY": ENTREZ_QUERY,
    }
    url = f"{BLAST_CGI}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=SUBMIT_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except Exception as e:
        return {"ok": False, "rid": None, "reason": f"http error: {e}"[:200]}

    rid = re.search(r"RID = (\S+)", body)
    if rid:
        return {"ok": True, "rid": rid.group(1), "reason": "queued"}
    m = QUEUE_ERROR_RE.search(body)
    if m:
        snippet = re.sub(r"\s+", " ", body[max(0, m.start() - 40):m.start() + 120])
        return {"ok": False, "rid": None, "reason": f"queue rejected: {snippet.strip()}"[:200]}
    return {"ok": False, "rid": None, "reason": "no RID returned (unrecognized response)"}


def delete_rid(rid):
    """Best-effort cleanup so we don't leave orphaned jobs in the queue."""
    url = f"{BLAST_CGI}?CMD=Delete&RID={urllib.parse.quote(rid)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT):
            pass
    except Exception:
        pass


def submit_burst(seq):
    """Sample the submit/queue path several times. Returns aggregate dict."""
    results = []
    for i in range(BURST_N):
        if i:
            time.sleep(BURST_SPACING)
        results.append(submit_once(seq))
    for r in results:
        if r["rid"]:
            delete_rid(r["rid"])
    ok = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    return {
        "total": len(results),
        "ok": ok,
        "failed": len(failed),
        "sample_error": failed[0]["reason"] if failed else "",
    }


def run_blast(seq):
    """Run one end-to-end blastn -remote. Return retrieval-health dict."""
    tmp = Path(tempfile.gettempdir()) / "blast_query.fasta"
    tmp.write_text(f">query\n{seq}\n")
    cmd = [
        "blastn", "-remote", "-db", DB, "-query", str(tmp),
        "-task", "megablast", "-evalue", "1e-5",
        "-max_target_seqs", "5", "-outfmt", "6",
    ]
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=BLAST_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"available": True, "ok": False,
                "latency_s": round(time.monotonic() - start, 1),
                "hit_count": 0, "detail": f"timeout after {BLAST_TIMEOUT}s"}
    except FileNotFoundError:
        return {"available": False, "ok": False, "latency_s": 0,
                "hit_count": 0, "detail": "blastn not installed"}

    latency = round(time.monotonic() - start, 1)
    if proc.returncode != 0:
        detail = (proc.stderr or "nonzero exit").strip().splitlines()
        return {"available": True, "ok": False, "latency_s": latency,
                "hit_count": 0,
                "detail": (detail[-1] if detail else "nonzero exit")[:300]}
    hits = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return {"available": True, "ok": True, "latency_s": latency,
            "hit_count": len(hits), "detail": "ok"}


def classify(submit, blast, eutils_ok, neutral_ok):
    """Map submit + retrieval + context probes to a status and message."""
    if not neutral_ok:
        return ("INCONCLUSIVE",
                "The monitor's own network could not reach the internet; "
                "cannot judge NCBI. Ignore this check.")

    total, ok, failed = submit["total"], submit["ok"], submit["failed"]
    scope = ("the rest of NCBI is reachable, so this looks BLAST-specific"
             if eutils_ok else
             "NCBI eutils is also unreachable, so this looks NCBI-wide")

    # Submit stage: this is where the reported queue failure lives.
    if total > 0 and ok == 0:
        return ("OUTAGE",
                f"All {total} test submissions to the BLAST queue failed; "
                f"{scope}. (e.g. {submit['sample_error']})")
    if failed > 0:
        return ("DEGRADED",
                f"Intermittent BLAST queue failures: {failed} of {total} test "
                f"submissions were rejected. Some users will hit errors. "
                f"(e.g. {submit['sample_error']})")

    # Submits all queued. Now judge retrieval (if we could run it).
    if not blast["available"]:
        return ("OPERATIONAL",
                f"BLAST job submission is working ({ok}/{total} queued). "
                "End-to-end retrieval not checked in this run.")
    if not blast["ok"]:
        return ("DEGRADED",
                f"Jobs submit fine but results did not come back "
                f"({blast['detail']}); {scope}.")
    if blast["hit_count"] == 0:
        return ("SLOW", "BLAST responded but returned no hits (unexpected).")
    if blast["latency_s"] > SLOW_THRESHOLD:
        return ("SLOW",
                f"BLAST is working but slow ({blast['latency_s']:.0f}s to "
                "return results).")
    return ("OPERATIONAL",
            f"Submissions queue ({ok}/{total}) and results return normally in "
            f"{blast['latency_s']:.0f}s.")


def probe():
    neutral_ok = http_ok(NEUTRAL_URL)
    eutils_ok = http_ok(EUTILS_URL)
    seq, seq_src = fetch_query_seq()

    submit = submit_burst(seq) if seq else {
        "total": 0, "ok": 0, "failed": 0, "sample_error": "no query sequence"}

    blast = run_blast(seq) if seq else {
        "available": False, "ok": False, "latency_s": 0, "hit_count": 0,
        "detail": "no query sequence"}
    if blast["available"] and not blast["ok"] and "timeout" not in blast["detail"]:
        time.sleep(15)  # one retrieval retry (submit health already sampled)
        blast = run_blast(seq)

    status, message = classify(submit, blast, eutils_ok, neutral_ok)
    return {
        "status": status,
        "checked_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "submit_total": submit["total"],
        "submit_ok": submit["ok"],
        "submit_failed": submit["failed"],
        "submit_sample_error": submit["sample_error"],
        "blast_ok": blast["ok"],
        "blast_latency_s": blast["latency_s"],
        "hit_count": blast["hit_count"],
        "eutils_ok": eutils_ok,
        "neutral_ok": neutral_ok,
        "query_source": seq_src,
        "message": message,
    }


def update_history(record):
    try:
        history = json.loads(HISTORY_FILE.read_text())
        if not isinstance(history, list):
            history = []
    except (FileNotFoundError, json.JSONDecodeError):
        history = []
    history.append({
        "t": record["checked_utc"],
        "s": record["status"],
        "l": record["blast_latency_s"],
        "ok": record["blast_ok"],
        "sf": record["submit_failed"],
        "st": record["submit_total"],
    })
    history = history[-HISTORY_LIMIT:]
    HISTORY_FILE.write_text(json.dumps(history, indent=0))


def main():
    DOCS.mkdir(exist_ok=True)
    record = probe()
    STATUS_FILE.write_text(json.dumps(record, indent=2))
    update_history(record)
    print(json.dumps(record, indent=2))
    # A detected outage is not a monitor error, so never fail the CI job here.
    return 0


if __name__ == "__main__":
    sys.exit(main())
