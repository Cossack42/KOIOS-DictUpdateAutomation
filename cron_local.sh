#!/bin/bash
# KOIOS DictUpdateAutomation — Local cron backup
# ─────────────────────────────────────────────────────────────────────────────
# Backup runner for when GitHub Actions is unavailable.
# Runs iso_metadata_watcher.py, then commits and pushes any changes.
#
# Setup (run once):
#   chmod +x cron_local.sh
#
# Add to crontab (crontab -e) to run on 1st and 15th at 06:00:
#   0 6 1,15 * * /path/to/KOIOS/KOIOS\ DictUpdateAutomation/cron_local.sh \
#     >> /path/to/KOIOS/KOIOS\ DictUpdateAutomation/logs/cron_local.log 2>&1
# ─────────────────────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "=== ISO Metadata Watch: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

python3 iso_metadata_watcher.py

git add data/ logs/

if git diff --staged --quiet; then
    echo "Nothing to commit — no changes this fortnight."
else
    SNAPSHOT_DATE=$(date +%Y-%m-%d)
    git commit -m "chore: ISO metadata snapshot ${SNAPSHOT_DATE}

See logs/change_log.jsonl for detail."
    git push
    echo "Committed and pushed snapshot ${SNAPSHOT_DATE}."
fi
