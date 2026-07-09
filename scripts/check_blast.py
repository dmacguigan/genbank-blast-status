#!/usr/bin/env python3
"""Probe NCBI GenBank remote BLAST and write a downdetector-style status.

Runs three layered checks so a real BLAST outage can be told apart from a
NCBI-wide problem or a broken runner network:

  1. neutral connectivity (example.com)  -> is *our* network alive?
  2. NCBI eutils (einfo)                  -> is NCBI (not BLAST) alive?
  3. real `blastn -remote` against nt     -> is BLAST itself working?

Status is based on whether the BLAST search *completes* (not on hit count),
plus latency. Results are written to docs/status.json and appended to a
rolling docs/history.json.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
STATUS_FILE = DOCS / "status.json"
HISTORY_FILE = DOCS / "history.json"
FALLBACK_QUERY = ROOT / "scripts" / "query.fasta"

# Pinned RefSeq used as the BLAST query; fetched fresh so hits are guaranteed.
QUERY_ACC = "NM_002046"  # human GAPDH mRNA, one of the most-sequenced genes
NEUTRAL_URL = "https://example.com"
EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi"
EFETCH_URL = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    f"?db=nuccore&id={QUERY_ACC}&rettype=fasta&retmode=text"
)

USER_AGENT = "genbank-blast-status/1.0 (github actions monitor)"
HTTP_TIMEOUT = 30          # seconds for the lightweight GET probes
BLAST_TIMEOUT = 300        # seconds hard cap on the remote BLAST
SLOW_THRESHOLD = 180       # BLAST latency above this (s) => SLOW
HISTORY_LIMIT = 336        # ~7 days at one check / 30 min


def http_ok(url, timeout=HTTP_TIMEOUT):
    """Return True if the URL returns HTTP 200 with some body."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200 and bool(resp.read(1))
    except Exception:
        return False


def fetch_query():
    """Fetch the pinned query FASTA from NCBI; fall back to committed copy.

    Returns (path, source) where source is 'efetch' or 'fallback'.
    """
    req = urllib.request.Request(EFETCH_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = resp.read().decode("utf-8", "replace")
        if data.startswith(">") and len(data.splitlines()) > 1:
            tmp = Path(tempfile.gettempdir()) / "blast_query.fasta"
            tmp.write_text(data)
            return tmp, "efetch"
    except Exception:
        pass
    return FALLBACK_QUERY, "fallback"


def run_blast(query_path):
    """Run blastn -remote. Return dict with ok, latency_s, hit_count, detail."""
    cmd = [
        "blastn", "-remote", "-db", "nt",
        "-query", str(query_path),
        "-task", "megablast",
        "-evalue", "1e-5",
        "-max_target_seqs", "5",
        "-outfmt", "6",
    ]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=BLAST_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "latency_s": round(time.monotonic() - start, 1),
            "hit_count": 0,
            "detail": f"timeout after {BLAST_TIMEOUT}s",
        }
    except FileNotFoundError:
        return {"ok": False, "latency_s": 0, "hit_count": 0,
                "detail": "blastn not installed"}

    latency = round(time.monotonic() - start, 1)
    if proc.returncode != 0:
        detail = (proc.stderr or "nonzero exit").strip().splitlines()
        return {"ok": False, "latency_s": latency, "hit_count": 0,
                "detail": (detail[-1] if detail else "nonzero exit")[:300]}

    hits = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return {"ok": True, "latency_s": latency, "hit_count": len(hits),
            "detail": "ok"}


def classify(blast, eutils_ok, neutral_ok):
    """Map probe results to a status + human message."""
    if blast["ok"]:
        if blast["hit_count"] == 0:
            return ("SLOW",
                    "BLAST responded but returned no hits (unexpected).")
        if blast["latency_s"] > SLOW_THRESHOLD:
            return ("SLOW",
                    f"BLAST is working but slow ({blast['latency_s']:.0f}s to "
                    "return results).")
        return ("OPERATIONAL",
                f"Remote BLAST returned results normally in "
                f"{blast['latency_s']:.0f}s.")

    # BLAST failed. Use the other probes to locate the fault.
    if not neutral_ok:
        return ("INCONCLUSIVE",
                "Monitor's own network could not reach the internet; "
                "cannot judge NCBI. Ignore this check.")
    if eutils_ok:
        return ("DEGRADED",
                "Remote BLAST failed but the rest of NCBI is reachable. "
                f"Likely a BLAST-specific problem ({blast['detail']}).")
    return ("OUTAGE",
            "Remote BLAST failed and NCBI eutils is also unreachable. "
            "Likely a broad NCBI outage.")


def probe():
    neutral_ok = http_ok(NEUTRAL_URL)
    eutils_ok = http_ok(EUTILS_URL)
    query_path, query_src = fetch_query()

    blast = run_blast(query_path)
    if not blast["ok"] and blast["detail"] != "blastn not installed":
        # One retry to avoid a single transient hiccup flipping status.
        time.sleep(15)
        blast = run_blast(query_path)

    status, message = classify(blast, eutils_ok, neutral_ok)
    return {
        "status": status,
        "checked_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "blast_ok": blast["ok"],
        "blast_latency_s": blast["latency_s"],
        "hit_count": blast["hit_count"],
        "eutils_ok": eutils_ok,
        "neutral_ok": neutral_ok,
        "query_source": query_src,
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
    })
    history = history[-HISTORY_LIMIT:]
    HISTORY_FILE.write_text(json.dumps(history, indent=0))


def main():
    DOCS.mkdir(exist_ok=True)
    record = probe()
    STATUS_FILE.write_text(json.dumps(record, indent=2))
    update_history(record)
    print(json.dumps(record, indent=2))
    # Never fail the CI job on a detected outage; a nonzero exit here would
    # just mean "monitor errored", which is different from "BLAST is down".
    return 0


if __name__ == "__main__":
    sys.exit(main())
