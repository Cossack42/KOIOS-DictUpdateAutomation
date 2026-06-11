# KOIOS DictUpdateAutomation

Fortnightly watcher for the [ISO Deliverables Metadata](https://www.iso.org/open-data.html#iso_deliverables_metadata) dataset — the source of truth for the KOIOS Concept Dictionary.

On each run, the watcher downloads the latest CSV, diffs it record-by-record against the stored version, logs all changes, and commits the updated file. Committing the updated file to this repo is the trigger for downstream DB automation.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Repository Structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Usage](#usage)
- [Change Log Format](#change-log-format)
- [Scheduling](#scheduling)
  - [GitHub Actions (primary)](#github-actions-primary)
  - [Local Cron (backup)](#local-cron-backup)
- [Known Issues & Flags](#known-issues--flags)
  - [Large File Warning](#large-file-warning)
  - [SQL Table Consideration](#sql-table-consideration)
- [Adding a New Source](#adding-a-new-source)

---

## How It Works

```
Trigger (GH Actions fortnightly cron / local cron)
        ↓
Download latest ISO Deliverables Metadata CSV
        ↓
SHA-256 hash check → identical to stored version? Exit early.
        ↓
Record-level diff (keyed on id column)
        ↓
Categorise changes:
  ├── added     — new id not in stored version
  ├── modified  — existing id, one or more fields changed
  └── withdrawn — currentStage transitioned to 9599
        ↓
Append structured entries to logs/change_log.jsonl
        ↓
Overwrite data/iso_deliverables_metadata.csv with latest version
        ↓
Commit updated file + change log to repo
        ↓
↑ Downstream DB automation watches repo, picks up commit, acts on it
```

The Python script (`iso_metadata_watcher.py`) handles everything up to and including saving the updated file. The git commit and push are handled by the calling workflow (GitHub Actions or `cron_local.sh`) — keeping the script environment-agnostic.

---

## Repository Structure

```
KOIOS DictUpdateAutomation/
├── iso_metadata_watcher.py           ← core watcher script
├── data/
│   └── iso_deliverables_metadata.csv ← stored source of truth (committed fortnightly)
├── logs/
│   └── change_log.jsonl              ← append-only structured change log
├── .github/
│   └── workflows/
│       └── fortnightly_watch.yml     ← GH Actions cron schedule
├── cron_local.sh                     ← local cron backup runner
├── resources/                        ← reference files (not tracked by git)
└── .gitignore
```

---

## Prerequisites

Python 3.11+ and the `requests` library:

```bash
pip install requests
```

No other dependencies. The script uses only the standard library beyond `requests`.

---

## Usage

**Normal run:**
```bash
python iso_metadata_watcher.py
```

**Dry run — diff and print only, no files written:**
```bash
python iso_metadata_watcher.py --dry-run
```

Use `--dry-run` to verify the diff logic is working before committing to a full run. It prints a summary and a sample of the first 20 changes without modifying any files.

**First run (no stored version):**

If `data/iso_deliverables_metadata.csv` does not exist, the script treats the downloaded file as a baseline — saves it to `data/` and exits without logging any changes. This is the intended behaviour for initial setup.

---

## Change Log Format

`logs/change_log.jsonl` is an append-only file — one JSON object per line, one line per change event.

**Added record:**
```json
{
  "timestamp": "2026-06-25T06:01:14Z",
  "snapshot_date": "2026-06-25",
  "change_type": "added",
  "id": "12345",
  "reference": "ISO 12345:2026",
  "summary": {
    "reference": "ISO 12345:2026",
    "title.en": "Example standard title",
    "deliverableType": "IS",
    "currentStage": "6060",
    "ownerCommittee": "ISO/TC 999",
    "publicationDate": "2026-06-01"
  }
}
```

**Modified record:**
```json
{
  "timestamp": "2026-06-25T06:01:14Z",
  "snapshot_date": "2026-06-25",
  "change_type": "modified",
  "id": "12345",
  "reference": "ISO 12345:2026",
  "changes": {
    "title.en": { "old": "Old title", "new": "Revised title" },
    "edition":  { "old": "1", "new": "2" }
  }
}
```

**Withdrawn record:**
```json
{
  "timestamp": "2026-06-25T06:01:14Z",
  "snapshot_date": "2026-06-25",
  "change_type": "withdrawn",
  "id": "12345",
  "reference": "ISO 12345:2026",
  "changes": {
    "currentStage": { "old": "6060", "new": "9599" }
  }
}
```

Withdrawal is detected when `currentStage` transitions to `9599` (the ISO stage code for withdrawn/cancelled). It supersedes `modified` — if a record changes and one of those changes is a withdrawal, it is logged as `withdrawn`, not `modified`.

---

## Scheduling

### GitHub Actions (primary)

The workflow at `.github/workflows/fortnightly_watch.yml` runs automatically on the **1st and 15th of each month at 06:00 UTC**.

It can also be triggered manually from the GitHub UI via **Actions → ISO Metadata Fortnightly Watch → Run workflow**.

The workflow:
1. Checks out the repo
2. Runs `iso_metadata_watcher.py`
3. Stages `data/` and `logs/`
4. Commits and pushes only if there are changes, with a summary commit message

No secrets or environment variables are required — the download URL is hardcoded in the script.

### Local Cron (backup)

`cron_local.sh` is a backup runner for when GitHub Actions is unavailable or a manual local run is needed.

**Setup (run once):**
```bash
chmod +x cron_local.sh
```

**Add to crontab** (`crontab -e`) to mirror the GH Actions schedule:
```
0 6 1,15 * * /path/to/KOIOS/KOIOS\ DictUpdateAutomation/cron_local.sh >> /path/to/KOIOS/KOIOS\ DictUpdateAutomation/logs/cron_local.log 2>&1
```

The local log (`logs/cron_local.log`) is excluded from git via `.gitignore`.

---

## Known Issues & Flags

### Large File Warning

> ⚠️ **The CSV is currently ~56 MB**, which exceeds GitHub's recommended 50 MB soft limit for individual files. GitHub will warn on each push but will not block it.

As ISO publishes more standards, this file will grow. Mitigation options in order of preference:

**Option 1 — Git LFS (recommended)**

Store the CSV as a Git Large File Storage object. Git history stays clean, file is tracked properly, and GitHub provides 1 GB LFS storage on the free tier.

```bash
git lfs install
git lfs track "data/*.csv"
git add .gitattributes
git commit -m "Track CSV with Git LFS"
```

**Option 2 — Store in DB, commit only the hash**

If a SQL database is adopted (see below), the canonical copy of the CSV lives in the DB rather than the repo. The repo would commit only a `data/iso_deliverables_metadata.sha256` hash file — lightweight, still provides a change signal for downstream automation, and avoids any file size issue entirely.

---

### SQL Table Consideration

> 💡 **Future consideration:** store the ISO Deliverables Metadata in a SQL table rather than (or in addition to) a flat CSV in the repo.

**Motivation:**
- The CSV is already 80 000+ rows and grows continuously
- A SQL table enables efficient querying (filter by `currentStage`, `ownerCommittee`, `deliverableType`, etc.) without loading the full file
- DB automation can apply row-level updates (INSERT / UPDATE / soft-delete for withdrawals) rather than swapping out an entire file
- Eliminates the Git LFS file size problem entirely

**Proposed approach if adopted:**

| Aspect | Detail |
|---|---|
| Table name | `iso_deliverables` |
| Primary key | `id` (integer, matches CSV column) |
| Withdrawn records | Set `currentStage = 9599` and a `deprecated_at` timestamp — never hard-deleted |
| Trigger | Watcher script upserts rows directly after diffing, or commits the diff to the repo for the DB automation to consume |
| Change log | `change_log.jsonl` continues to serve as the audit trail regardless |

If this route is taken, the watcher script would need a DB connection (e.g. `psycopg2` for PostgreSQL, `pyodbc` for SQL Server) and an upsert function alongside the existing diff logic. The git commit of the change log alone would then serve as the downstream trigger, with the CSV either dropped from the repo or replaced by its hash.

---

## Adding a New Source

If a second metadata source needs to be tracked alongside ISO Deliverables Metadata, follow this pattern:

1. Add a new downloader + diff function in `iso_metadata_watcher.py` (or create a separate `{source}_watcher.py`)
2. Store the baseline in `data/{source}_metadata.csv`
3. Append to the same `logs/change_log.jsonl` with a `source` field added to each event
4. Add the new `data/` file to the GH Actions `git add` step
