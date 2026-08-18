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
