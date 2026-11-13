#!/bin/sh
# Run Metis's two environments. Lives next to compose.yml and .env.
#
#   metis.sh update     Download new images. Back up and restart prod when its
#                       image changed; restart qa when it is running and changed.
#                       Quiet when there is nothing new, so it suits a schedule.
#   metis.sh qa-start   Start qa with a fresh copy of prod's data.
#   metis.sh qa-stop    Stop qa.
#   metis.sh backup     Back up prod's database now.
#   metis.sh status     Show what is running.
set -eu

cd "$(dirname "$0")"
DOCKER=${DOCKER:-/usr/local/bin/docker}
[ -f .env ] && . ./.env
PROD_PORT=${PROD_PORT:-8101}
QA_PORT=${QA_PORT:-8102}
KEEP_BACKUPS=${KEEP_BACKUPS:-14}
DB=finance.duckdb

compose() { "$DOCKER" compose --profile qa "$@"; }
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# The image a service is configured to run, and the image ID behind it now.
configured_image() { compose config --images "$1" 2>/dev/null | head -n 1; }
image_id() { "$DOCKER" image inspect --format '{{.Id}}' "$1" 2>/dev/null || true; }
running() { [ -n "$(compose ps --status running --quiet "$1" 2>/dev/null)" ]; }

# Copy a stopped environment's database, including any write-ahead log.
copy_db() {
  from=$1 to=$2
  [ -f "$from/$DB" ] || return 0
  mkdir -p "$(dirname "$to")"
  cp -p "$from/$DB" "$to"
  if [ -f "$from/$DB.wal" ]; then cp -p "$from/$DB.wal" "$to.wal"; else rm -f "$to.wal"; fi
}

backup_stopped_prod() {
  [ -f "prod/$DB" ] || return 0
  target="backups/finance-$(date +%Y%m%d-%H%M%S).duckdb"
  copy_db prod "$target"
  log "backed up prod to $target"
  # Keep the newest KEEP_BACKUPS.
  ls -1t backups/finance-*.duckdb 2>/dev/null | tail -n +"$((KEEP_BACKUPS + 1))" | while read -r old; do
    rm -f "$old" "$old.wal"
  done
}

# Wait for an environment to answer, so a broken release is noticed.
wait_healthy() {
  service=$1 port=$2 tries=30
  while [ "$tries" -gt 0 ]; do
    if curl -fs -o /dev/null "http://127.0.0.1:$port/login"; then
      log "$service is up"
      return 0
    fi
    tries=$((tries - 1))
    sleep 2
  done
  log "ERROR: $service did not answer on port $port; see: $DOCKER compose logs $service"
  return 1
}

pull_changed() {
  service=$1
  image=$(configured_image "$service")
  before=$(image_id "$image")
  compose pull --quiet "$service" >/dev/null 2>&1 || { log "ERROR: could not download $image"; return 1; }
  [ "$before" != "$(image_id "$image")" ]
}

update() {
  if pull_changed prod; then
    log "new prod image"
    compose stop prod >/dev/null 2>&1 || true
    backup_stopped_prod
    compose up -d prod >/dev/null
    wait_healthy prod "$PROD_PORT"
  fi
  if pull_changed qa && running qa; then
    log "new qa image"
    compose up -d qa >/dev/null
    wait_healthy qa "$QA_PORT"
  fi
  # Remove superseded Metis images only, never another app's.
  "$DOCKER" image prune -f --filter "label=org.opencontainers.image.title=metis" >/dev/null 2>&1 || true
}

qa_start() {
  pull_changed qa || true
  compose stop qa >/dev/null 2>&1 || true
  if [ -f "prod/$DB" ]; then
    # Prod stops for a few seconds so the copy is consistent.
    compose stop prod >/dev/null
    copy_db prod "qa/$DB"
    compose up -d prod >/dev/null
    log "copied prod's data to qa"
  fi
  compose up -d qa >/dev/null
  wait_healthy qa "$QA_PORT"
}

case "${1:-}" in
  update) update ;;
  qa-start) qa_start ;;
  qa-stop) compose stop qa >/dev/null && log "qa stopped" ;;
  backup)
    compose stop prod >/dev/null
    backup_stopped_prod
    compose up -d prod >/dev/null
    wait_healthy prod "$PROD_PORT"
    ;;
  status) compose ps ;;
  *)
    echo "usage: $0 update | qa-start | qa-stop | backup | status" >&2
    exit 2
    ;;
esac
