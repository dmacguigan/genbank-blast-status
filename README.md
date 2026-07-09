# GenBank Remote BLAST Status

A tiny, free-to-host "downdetector" for NCBI GenBank's **remote BLAST**
(`blastn -remote` against the `nt` database).

A GitHub Actions cron job runs a real remote BLAST query every ~30 minutes,
records whether it worked and how long it took, and commits the result. A
static site (GitHub Pages) reads that data and shows a colored status banner
plus a timeline of recent checks. No server, no cost.

## How it works

Each run of [`scripts/check_blast.py`](scripts/check_blast.py) does three
layered checks so a real BLAST problem can be told apart from other faults:

1. **Neutral connectivity** (`example.com`) - is the runner's network alive?
2. **NCBI eutils** (`einfo`) - is NCBI itself reachable (separate from BLAST)?
3. **Real BLAST** - `blastn -remote -db nt` on a small query, with a hard
   timeout and one retry.

Status is based on whether the BLAST search *completes* and how fast, not on
hit count:

| Status | Meaning |
| --- | --- |
| **OPERATIONAL** | BLAST returned results normally. |
| **SLOW** | BLAST worked but was slow (>180s), or returned no hits. |
| **DEGRADED** | BLAST failed but the rest of NCBI is up (BLAST-specific issue). |
| **OUTAGE** | BLAST failed *and* NCBI eutils is unreachable (broad NCBI outage). |
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

This runs **one small query every 30 minutes** (~48/day), comparable to a
single interactive user, which is well within NCBI's usage policy. `blastn
-remote` uses NCBI's BLAST URL API under the hood; the CLI cannot attach the
`tool=`/`email=` identifiers a raw URL-API call would, but the load is
trivial. **Please do not lower the interval below 30 minutes.** See NCBI's
[usage guidelines](https://blast.ncbi.nlm.nih.gov/doc/blast-help/developerinfo.html).

This project is **not affiliated with NCBI**.
