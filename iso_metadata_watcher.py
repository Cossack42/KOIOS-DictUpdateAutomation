#!/usr/bin/env python3
"""
KOIOS DictUpdateAutomation — ISO Deliverables Metadata Watcher
===============================================================
Downloads the latest ISO Deliverables Metadata CSV fortnightly, diffs it
against the stored version, logs all changes, and saves the updated file.

The stored file (data/iso_deliverables_metadata.csv) acts as the source of
truth for the KOIOS Concept Dictionary. Committing an update to it is what
triggers the downstream DB automation — this script only manages data and logs.

Change types logged:
  added     — new id not present in stored version
  modified  — existing id with one or more changed field values
  withdrawn — currentStage changed to 9599 (supersedes modified)

Usage:
    python iso_metadata_watcher.py              # normal run
    python iso_metadata_watcher.py --dry-run    # diff and print only, no writes
"""

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import requests

# ── Configuration ─────────────────────────────────────────────────────────────

# Direct download URL for the ISO Deliverables Metadata CSV.
# Find it at: https://www.iso.org/open-data.html#iso_deliverables_metadata
DOWNLOAD_URL = "FILL_IN_DIRECT_CSV_DOWNLOAD_URL"

DATA_DIR  = Path("data")
DATA_FILE = DATA_DIR / "iso_deliverables_metadata.csv"
LOG_DIR   = Path("logs")
CHANGE_LOG = LOG_DIR / "change_log.jsonl"

ID_COL          = "id"
STAGE_COL       = "currentStage"
REF_COL         = "reference"
WITHDRAWN_STAGE = "9599"

# Fields included in the summary block of added-record log entries
SUMMARY_FIELDS = [
    "reference", "title.en", "deliverableType",
    "currentStage", "ownerCommittee", "publicationDate",
]

# ── Download ──────────────────────────────────────────────────────────────────

def download_latest() -> bytes:
    """Download the latest ISO Deliverables Metadata CSV."""
    if DOWNLOAD_URL.startswith("FILL_IN"):
        print("ERROR: DOWNLOAD_URL is not configured.")
        print("Set it in iso_metadata_watcher.py to the direct CSV download URL")
        print("from https://www.iso.org/open-data.html#iso_deliverables_metadata")
        sys.exit(1)

    print(f"Downloading latest ISO Deliverables Metadata …")
    resp = requests.get(DOWNLOAD_URL, timeout=60, headers={"Accept": "text/csv"})
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "html" in content_type:
        print("ERROR: Download URL returned HTML, not CSV.")
        print("Check that DOWNLOAD_URL points to the direct file, not a landing page.")
        sys.exit(1)

    return resp.content


# ── Hashing ───────────────────────────────────────────────────────────────────

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── CSV loading ───────────────────────────────────────────────────────────────

def load_csv_bytes(data: bytes) -> dict[str, dict]:
    """Parse CSV bytes into {id: row_dict}."""
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(StringIO(text))
    records = {}
    for row in reader:
        row_id = row.get(ID_COL, "").strip()
        if row_id:
            records[row_id] = {k: (v or "").strip() for k, v in row.items()}
    return records


def load_csv_file(path: Path) -> dict[str, dict]:
    """Load stored CSV from disk into {id: row_dict}."""
    if not path.exists():
        return {}
    return load_csv_bytes(path.read_bytes())


# ── Diffing ───────────────────────────────────────────────────────────────────

def diff_records(
    old: dict[str, dict],
    new: dict[str, dict],
) -> list[dict]:
    """
    Compare old and new record sets. Returns a list of change event dicts.
    withdrawn supersedes modified when currentStage transitions to 9599.
    """
    now           = datetime.now(timezone.utc).isoformat()
    snapshot_date = datetime.now(timezone.utc).date().isoformat()
    events        = []

    old_ids = set(old.keys())
    new_ids = set(new.keys())

    # ── Added ─────────────────────────────────────────────────────────────────
    for row_id in sorted(new_ids - old_ids):
        row = new[row_id]
        events.append({
            "timestamp":     now,
            "snapshot_date": snapshot_date,
            "change_type":   "added",
            "id":            row_id,
            "reference":     row.get(REF_COL, ""),
            "summary":       {f: row.get(f, "") for f in SUMMARY_FIELDS},
        })

    # ── Modified / Withdrawn ──────────────────────────────────────────────────
    for row_id in sorted(old_ids & new_ids):
        old_row = old[row_id]
        new_row = new[row_id]

        changes = {
            col: {"old": old_row.get(col, ""), "new": new_row.get(col, "")}
            for col in new_row
            if old_row.get(col, "") != new_row.get(col, "")
        }

        if not changes:
            continue

        old_stage = old_row.get(STAGE_COL, "")
        new_stage = new_row.get(STAGE_COL, "")
        change_type = (
            "withdrawn"
            if new_stage == WITHDRAWN_STAGE and old_stage != WITHDRAWN_STAGE
            else "modified"
        )

        events.append({
            "timestamp":     now,
            "snapshot_date": snapshot_date,
            "change_type":   change_type,
            "id":            row_id,
            "reference":     new_row.get(REF_COL, ""),
            "changes":       changes,
        })

    return events


# ── Logging ───────────────────────────────────────────────────────────────────

def append_log(events: list[dict]):
    """Append change events to the JSONL change log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with CHANGE_LOG.open("a", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def print_summary(events: list[dict]):
    counts = {"added": 0, "modified": 0, "withdrawn": 0}
    for e in events:
        counts[e["change_type"]] = counts.get(e["change_type"], 0) + 1
    print(f"  Added:     {counts['added']}")
    print(f"  Modified:  {counts['modified']}")
    print(f"  Withdrawn: {counts['withdrawn']}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="KOIOS DictUpdateAutomation — ISO Deliverables Metadata Watcher",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Diff and print only — do not write any files",
    )
    return p


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = build_parser().parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Download
    new_bytes = download_latest()
    print(f"Downloaded {len(new_bytes):,} bytes")

    # 2. Early exit if file is identical to stored version
    new_hash    = sha256(new_bytes)
    stored_hash = sha256(DATA_FILE.read_bytes()) if DATA_FILE.exists() else None

    if stored_hash and new_hash == stored_hash:
        print("No changes — file hash matches stored version. Nothing to do.")
        sys.exit(0)

    # 3. Load both versions
    old_records = load_csv_file(DATA_FILE)
    new_records = load_csv_bytes(new_bytes)

    print(f"Stored records : {len(old_records):,}")
    print(f"Latest records : {len(new_records):,}")

    # 4. First run — no stored version, save as baseline
    if not old_records:
        print("No stored version found — saving as baseline (no changes logged).")
        if not args.dry_run:
            DATA_FILE.write_bytes(new_bytes)
            print(f"Baseline saved → {DATA_FILE}")
        else:
            print("[dry-run] Baseline not written.")
        sys.exit(0)

    # 5. Diff
    print("Diffing records …")
    events = diff_records(old_records, new_records)

    if not events:
        print("No record-level changes detected.")
        if not args.dry_run:
            DATA_FILE.write_bytes(new_bytes)
        sys.exit(0)

    print(f"\nChanges detected:")
    print_summary(events)

    # 6. Dry-run — print sample and exit
    if args.dry_run:
        print("\n[dry-run] Sample (first 20 changes):")
        for e in events[:20]:
            print(f"  {e['change_type']:10}  {e['reference']}")
        if len(events) > 20:
            print(f"  … and {len(events) - 20} more")
        print("\n[dry-run] No files written.")
        sys.exit(0)

    # 7. Save updated file and append to change log
    DATA_FILE.write_bytes(new_bytes)
    append_log(events)

    print(f"\nUpdated  → {DATA_FILE}")
    print(f"Logged   → {CHANGE_LOG}  ({len(events)} new entries)")


if __name__ == "__main__":
    main()
