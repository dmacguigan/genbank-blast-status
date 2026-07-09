# GenBank Remote BLAST Status

A tiny, free-to-host "downdetector" for NCBI GenBank's **remote BLAST**
(`blastn -remote` against the `nt` database).

A GitHub Actions cron job runs a real remote BLAST query every ~30 minutes,
records whether it worked and how long it took, and commits the result. A
static site (GitHub Pages) reads that data and shows a colored status banner
plus a timeline of recent checks. No server, no cost.

## How it works

Each run of [`scripts/check_blast.py`](scripts/check_blast.py) measures two
independent signals, because outages appear at different stages:

- **Submit health** - a short *burst* of BLAST URL-API submissions mirroring a
  real command (`core_nt` + an `-entrez_query` filter). This is the stage where
  queue failures like `Could not queue request: DB Put Request error:
  sp_NewRequestEx failed` occur. Sampling a burst and counting *any* rejection
  catches intermittent queue failures that a single retried query would hide.
  Submitted test jobs are deleted afterward so nothing is left in the queue.
- **Retrieval health** - one real end-to-end `blastn -remote -db core_nt`
  search, to confirm a queued job actually completes and returns results, and
  how long it took.

Two supporting probes locate the fault when something breaks:

- **Neutral connectivity** (`example.com`) - is the runner's network alive?
- **NCBI eutils** (`einfo`) - is NCBI itself reachable (separate from BLAST)?

> **`core_nt` vs `nt`:** NCBI split the old `nt` into `core_nt` plus taxon
> volumes; they are different databases with different content, and `core_nt`
> is what the web interface and most current commands use. The monitor uses
> `core_nt` to match what real users query.

Status reflects submit health first, then retrieval:

| Status | Meaning |
| --- | --- |
| **OPERATIONAL** | All test submissions queued and results returned normally. |
| **SLOW** | Submits and retrieval work, but retrieval was slow (>180s) or returned no hits. |
| **DEGRADED** | *Some* submissions were rejected (intermittent queue failures), or jobs submit but results don't come back. Some users hit errors. |
| **OUTAGE** | *All* test submissions failed (BLAST unusable; message notes whether it looks BLAST-specific or NCBI-wide). |
| **INCONCLUSIVE** | The monitor's own network was down; NCBI not blamed. |

Output goes to [`docs/status.json`](docs/status.json) (latest) and
[`docs/history.json`](docs/history.json) (rolling ~7 days).

## Setup (one time)

1. Create a **public** GitHub repo (public = free Actions minutes) and push
   this folder.
2. **Settings > Actions > General > Workflow permissions** -> *Read and write
   permissions*.
3. **Settings > Pages** -> Source: *Deploy from a branch*, branch `main`,
   folder `/docs`.
4. **Actions** tab -> run *BLAST status check* via **Run workflow** to seed the
   first real result.
5. Site is live at `https://<user>.github.io/<repo>/`.

## Local testing

```bash
sudo apt-get install -y ncbi-blast+     # needs the BLAST+ tools + internet
python3 scripts/check_blast.py          # writes docs/status.json + history.json
python3 -m http.server -d docs 8000     # open http://localhost:8000
```

## NCBI usage note

Each check does a small burst of submissions (default 3, spaced 10s and
deleted afterward) plus one end-to-end search, every 30 minutes. That is a
handful of tiny queries per half hour, comparable to one interactive user and
well within NCBI's usage policy. Submitted test jobs are deleted so nothing
lingers in the queue. **Please do not lower the interval below 30 minutes or
raise `BURST_N` much.** See NCBI's
[usage guidelines](https://blast.ncbi.nlm.nih.gov/doc/blast-help/developerinfo.html).

This project is **not affiliated with NCBI**.
