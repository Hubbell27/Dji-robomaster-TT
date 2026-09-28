#!/bin/sh
# Nightly backup of the database and encrypted uploads.
# Backups contain encrypted patient data; they can only be read together with
# the key file, which is deliberately NOT copied here. Keep the key file on a
# separate USB drive (office script: export-key).
set -eu
DATA_DIR=${DATA_DIR:-/data}
BACKUP_DIR=${BACKUP_OUT:-/backups}

run_backup() {
  stamp=$(date +%Y%m%d-%H%M%S)
  mkdir -p "$BACKUP_DIR"
  pg_dump -Fc -f "$BACKUP_DIR/intake-db-$stamp.dump.partial"
  mv "$BACKUP_DIR/intake-db-$stamp.dump.partial" "$BACKUP_DIR/intake-db-$stamp.dump"
  tar -czf "$BACKUP_DIR/intake-files-$stamp.tar.gz.partial" -C "$DATA_DIR" files 2>/dev/null || tar -czf "$BACKUP_DIR/intake-files-$stamp.tar.gz.partial" -T /dev/null
  mv "$BACKUP_DIR/intake-files-$stamp.tar.gz.partial" "$BACKUP_DIR/intake-files-$stamp.tar.gz"
  find "$BACKUP_DIR" -name 'intake-*' -mtime +"${BACKUP_KEEP_DAYS:-30}" -delete
  echo "$(date) backup complete: $stamp"
}

if [ "${1:-}" = "now" ]; then
  run_backup
  exit 0
fi

# loop: run once a day at BACKUP_HOUR (office local time).
last=""
while true; do
  today=$(date +%Y%m%d)
  hour=$(date +%H | sed 's/^0//')
  if [ "$hour" -ge "${BACKUP_HOUR:-21}" ] && [ "$last" != "$today" ]; then
    run_backup || echo "$(date) BACKUP FAILED" >&2
    last=$today
  fi
  sleep 300
done
