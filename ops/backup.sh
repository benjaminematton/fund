#!/bin/sh
# Nightly snapshot of the fund. Invoked by fund-backup.service at 17:30 ET.
#
# `sqlite3 .backup` uses the SQLite backup API, which is WAL-safe with a live
# writer; a plain `cp` of a WAL-mode database is not.
#
# NO PRUNE. At ~86 KB a snapshot, a full year costs ~31 MB. A retention policy
# would buy nothing measurable while introducing the only destructive
# operation in the whole deployment. Revisit only if the DB grows by orders of
# magnitude.
#
# `date` here is fine: this is ops, outside the injected-Clock rule that governs
# business logic (CLAUDE.md conventions / scripts/check_purity.py scope).
set -eu

: "${FUND_DB:?FUND_DB not set}"
: "${FUND_BACKUP_DIR:?FUND_BACKUP_DIR not set}"

STAMP="$(date +%Y-%m-%d)"
mkdir -p "$FUND_BACKUP_DIR"

TMP="${FUND_BACKUP_DIR}/fund-${STAMP}.sqlite.tmp"
FINAL="${FUND_BACKUP_DIR}/fund-${STAMP}.sqlite"

rm -f "$TMP"
sqlite3 "$FUND_DB" ".backup '${TMP}'"

# Verify BEFORE the rename, so an interrupted or corrupt backup can never
# leave a partial file that looks like a valid snapshot.
CHECK="$(sqlite3 "$TMP" 'PRAGMA integrity_check' 2>/dev/null || echo failed)"
if [ "$CHECK" != "ok" ]; then
    rm -f "$TMP"
    echo "backup: integrity_check failed for ${FUND_DB} (got: ${CHECK})" >&2
    exit 1
fi

mv "$TMP" "$FINAL"
echo "backup: wrote ${FINAL}"

if [ -n "${FUND_JOURNALS:-}" ] && [ -d "$FUND_JOURNALS" ]; then
    JTMP="${FUND_BACKUP_DIR}/journals-${STAMP}.tar.gz.tmp"
    tar -czf "$JTMP" -C "$(dirname "$FUND_JOURNALS")" "$(basename "$FUND_JOURNALS")"
    mv "$JTMP" "${FUND_BACKUP_DIR}/journals-${STAMP}.tar.gz"
    echo "backup: wrote ${FUND_BACKUP_DIR}/journals-${STAMP}.tar.gz"
fi

# Seat traces. Enumerated like the journals above, never a directory sweep:
# what gets backed up is a decision, not whatever happens to sit under the
# state dir.
#
# STILL NO PRUNE, and it was measured rather than assumed: a real trace is
# ~6.5 KB, so 10-25 turns a day is 65-160 KB/day -- 16-40 MB a year, the same
# order as the DB snapshots the argument above was written for. A retention
# policy would introduce the only destructive operation in this deployment to
# save tens of megabytes.
#
# Traces are the corpus the day-review loop reads. Unbacked, they die with the
# droplet, which would make recording them nearly pointless -- a trace cannot
# be reconstructed after the fact.
if [ -n "${FUND_TRACES:-}" ] && [ -d "$FUND_TRACES" ]; then
    TTMP="${FUND_BACKUP_DIR}/traces-${STAMP}.tar.gz.tmp"
    tar -czf "$TTMP" -C "$(dirname "$FUND_TRACES")" "$(basename "$FUND_TRACES")"
    mv "$TTMP" "${FUND_BACKUP_DIR}/traces-${STAMP}.tar.gz"
    echo "backup: wrote ${FUND_BACKUP_DIR}/traces-${STAMP}.tar.gz"
fi
