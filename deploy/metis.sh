#!/bin/sh
# Run Metis's two environments, one at a time. Lives next to compose.yml and .env.
#
#   metis.sh qa       Switch to qa: stop prod, copy prod's data to qa, start qa.
#   metis.sh prod     Switch to prod: stop qa, back up prod, start prod.
#   metis.sh update   Download new images and restart whichever environment is
#                     running if its image changed (backing up prod first).
#                     Quiet when there is nothing new, so it suits a schedule.
#   metis.sh backup   Back up prod's database now.
#   metis.sh status   Show what is running.
set -eu

cd "$(dirname "$0")"
DOCKER=${DOCKER:-/usr/local/bin/docker}
[ -f .env ] && . ./.env
PROD_PORT=${PROD_PORT:-8101}
QA_PORT=${QA_PORT:-8102}
KEEP_BACKUPS=${KEEP_BACKUPS:-14}
DB=finance.duckdb
# Synology's Docker refuses to start a container whose data folder is missing.
mkdir -p prod qa

compose() { "$DOCKER" compose --profile qa "$@"; }
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# The image a service is configured to run, and the image ID behind it now.
configured_image() { compose config --images "$1" 2>/dev/null | head -n 1; }
image_id() { "$DOCKER" image inspect --format '{{.Id}}' "$1" 2>/dev/null || true; }
running() { [ -n "$(compose ps --status running --quiet "$1" 2>/dev/null)" ]; }
stop() { compose stop "$1" >/dev/null 2>&1 || true; }

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

# Wait for an environment to answer, so a broken release is noticed. The first
# start on a small server can take a few minutes.
wait_healthy() {
  service=$1 port=$2 tries=90
  while [ "$tries" -gt 0 ]; do
    if curl -fs -m 10 -o /dev/null "http://127.0.0.1:$port/login"; then
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

start_prod() {
  stop qa
  stop prod
  backup_stopped_prod
  compose up -d prod >/dev/null 2>&1
  wait_healthy prod "$PROD_PORT"
}

start_qa() {
  pull_changed qa || true
  stop qa
  stop prod
  if [ -f "prod/$DB" ]; then
    copy_db prod "qa/$DB"
    log "copied prod's data to qa"
  fi
  compose up -d qa >/dev/null 2>&1
  wait_healthy qa "$QA_PORT"
}

update() {
  # A stopped environment only downloads; it starts on the new image next time.
  if pull_changed prod && running prod; then
    log "new prod image"
    start_prod
  fi
  if pull_changed qa && running qa; then
    log "new qa image"
    compose up -d qa >/dev/null 2>&1
    wait_healthy qa "$QA_PORT"
  fi
  # Remove superseded Metis images only, never another app's.
  "$DOCKER" image prune -f --filter "label=org.opencontainers.image.title=metis" >/dev/null 2>&1 || true
}

backup() {
  if running prod; then
    stop prod
    backup_stopped_prod
    compose up -d prod >/dev/null 2>&1
    wait_healthy prod "$PROD_PORT"
  else
    backup_stopped_prod
  fi
}

case "${1:-}" in
  qa) start_qa ;;
  prod) start_prod ;;
  update) update ;;
  backup) backup ;;
  status) compose ps ;;
  *)
    echo "usage: $0 qa | prod | update | backup | status" >&2
    exit 2
    ;;
esac
