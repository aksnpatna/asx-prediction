#!/bin/bash
# Daily backup of ASX prediction database
# Run via cron: 0 2 * * * /home/aksai/projects/asx-prediction/backup_db.sh

BACKUP_DIR="/home/aksai/backups/asx"
mkdir -p "$BACKUP_DIR"

# Keep last 30 days
find "$BACKUP_DIR" -name "asx_*.sql.gz" -mtime +30 -delete

# Create backup
BACKUP_FILE="$BACKUP_DIR/asx_$(date +%Y%m%d_%H%M%S).sql.gz"
docker exec asx-db pg_dump -U asx_user asx | gzip > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    echo "$(date): Backup created: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
    # Log to file
    echo "$(date) - $BACKUP_FILE" >> "$BACKUP_DIR/backup.log"
else
    echo "$(date): BACKUP FAILED" >&2
    exit 1
fi
