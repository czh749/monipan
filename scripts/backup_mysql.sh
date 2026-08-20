#!/usr/bin/env sh
set -eu

# Create an atomic, private MySQL logical backup using the client already
# shipped in the mysql container. This script does not schedule itself.

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
backup_dir=${MONIPAN_BACKUP_DIR:-"$project_dir/backups"}
retention_days=${MONIPAN_BACKUP_RETENTION_DAYS:-7}

case "$retention_days" in
    ""|*[!0-9]*)
        echo "MONIPAN_BACKUP_RETENTION_DAYS must be a non-negative integer" >&2
        exit 2
        ;;
esac

for command_name in docker gzip sha256sum; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "Required command is unavailable: $command_name" >&2
        exit 2
    fi
done

umask 077
mkdir -p -- "$backup_dir"
backup_dir=$(CDPATH= cd -- "$backup_dir" && pwd -P)
case "$backup_dir" in
    ""|"/")
        echo "Refusing to use an unsafe backup directory" >&2
        exit 2
        ;;
esac

lock_dir="$backup_dir/.monipan-backup.lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
    echo "Another backup is running, or a stale lock exists: $lock_dir" >&2
    exit 1
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_file="$backup_dir/monipan-$timestamp.sql.gz"
checksum_file="$backup_file.sha256"
temporary_sql=
temporary_file=
temporary_checksum=
publish_complete=0
backup_published=0
checksum_published=0

cleanup() {
    [ -z "$temporary_sql" ] || rm -f -- "$temporary_sql"
    [ -z "$temporary_file" ] || rm -f -- "$temporary_file"
    [ -z "$temporary_checksum" ] || rm -f -- "$temporary_checksum"
    if [ "$publish_complete" -eq 0 ]; then
        [ "$backup_published" -eq 0 ] || rm -f -- "$backup_file"
        [ "$checksum_published" -eq 0 ] || rm -f -- "$checksum_file"
    fi
    rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

if [ -e "$backup_file" ] || [ -e "$checksum_file" ]; then
    echo "A backup with this timestamp already exists" >&2
    exit 1
fi

temporary_sql=$(mktemp "$backup_dir/.monipan-sql.XXXXXX")
temporary_file=$(mktemp "$backup_dir/.monipan-gzip.XXXXXX")
temporary_checksum=$(mktemp "$backup_dir/.monipan-sha256.XXXXXX")

cd "$project_dir"
docker compose exec -T mysql sh -c '
    MYSQL_PWD="$MYSQL_PASSWORD" exec mysqldump \
        --single-transaction \
        --quick \
        --no-tablespaces \
        --set-gtid-purged=OFF \
        -u"$MYSQL_USER" \
        "$MYSQL_DATABASE"
' > "$temporary_sql"

if [ ! -s "$temporary_sql" ]; then
    echo "Backup output is empty" >&2
    exit 1
fi
gzip -c "$temporary_sql" > "$temporary_file"
gzip -t "$temporary_file"

checksum_output=$(sha256sum < "$temporary_file")
checksum_value=${checksum_output%% *}
if [ -z "$checksum_value" ]; then
    echo "Could not calculate backup checksum" >&2
    exit 1
fi
printf '%s  %s\n' \
    "$checksum_value" \
    "$(basename -- "$backup_file")" > "$temporary_checksum"

mv -- "$temporary_file" "$backup_file"
backup_published=1
mv -- "$temporary_checksum" "$checksum_file"
checksum_published=1
(
    cd "$backup_dir"
    sha256sum -c "$(basename -- "$checksum_file")"
)
publish_complete=1
rm -f -- "$temporary_sql"

# Retention is deliberately limited to files created by this script and only
# inside the validated backup directory.
find "$backup_dir" -maxdepth 1 -type f \
    \( -name 'monipan-*.sql.gz' -o -name 'monipan-*.sql.gz.sha256' \) \
    -mtime "+$retention_days" -delete

cleanup
trap - EXIT HUP INT TERM
echo "Backup created: $backup_file"
echo "Checksum created: $checksum_file"
