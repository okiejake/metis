#!/bin/sh
# Run Metis's two environments, one at a time. Lives next to compose.yml and .env.
#
#   metis.sh prod     Switch to prod: download its latest image, stop qa,
#                     back up prod, start prod.
#   metis.sh qa       Switch to qa: download its latest image, stop prod,
#                     copy prod's data to qa, start qa.
#   metis.sh update   Download new images and restart whichever environment is
#                     running if its image changed (backing up prod first).
#   metis.sh backup   Back up prod's database now.
#   metis.sh status   Show what is running.
#
# Every command except status appends what it did to update.log next to this
# file, including "nothing new", so each run leaves a line.
set -eu

cd "$(dirname "$0")"
DOCKER=${DOCKER:-/usr/local/bin/docker}
[ -f .env ] && . ./.env
PROD_PORT=${PROD_PORT:-8101}
QA_PORT=${QA_PORT:-8102}
KEEP_BACKUPS=${KEEP_BACKUPS:-14}
LOG=${METIS_LOG:-update.log}
DB=finance.duckdb
CMD=${1:-}
PULL_FAILED=0
# Synology's Docker refuses to start a container whose data folder is missing.
mkdir -p prod qa

compose() { "$DOCKER" compose --profile qa "$@"; }

# Append to the log, and print too when run from a terminal. A task that still
# sends its output to the log gets each line once.
log() {
  line="$(date '+%Y-%m-%d %H:%M:%S') $*"
  echo "$line" >> "$LOG"
  if [ -t 1 ]; then echo "$line"; fi
}

# The image a service is configured to run, and the image ID behind it now.
configured_image() { compose config --images "$1" 2>/dev/null | head -n 1; }
image_id() { "$DOCKER" image inspect --format '{{.Id}}' "$1" 2>/dev/null || true; }
running() { [ -n "$(compose ps --status running --quiet "$1" 2>/dev/null)" ]; }
stop() { compose stop "$1" >/dev/null 2>&1 || true; }
up() { compose up -d "$1" >/dev/null 2>&1 || { log "ERROR: could not start $1"; return 1; }; }

# Copy a stopped environment's database, including any write-ahead log.
copy_db() {
  from=$1 to=$2
  [ -f "$from/$DB" ] || return 0
  mkdir -p "$(dirname "$to")"
  cp -p "$from/$DB" "$to"
  if [ -f "$from/$DB.wal" ]; then cp -p "$from/$DB.wal" "$to.wal"; else rm -f "$to.wal"; fi
}

backup_stopped_prod() {
  if [ ! -f "prod/$DB" ]; then
    log "no prod database to back up yet"
    return 0
  fi
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

# True when a new image was downloaded. A failed download is logged, counted,
# and treated as no change.
pull_changed() {
  service=$1
  image=$(configured_image "$service")
  before=$(image_id "$image")
  if ! compose pull --quiet "$service" >/dev/null 2>&1; then
    log "ERROR: could not download $image"
    PULL_FAILED=1
    return 1
  fi
  [ "$before" != "$(image_id "$image")" ]
}

# Restart prod on its current image, backing it up while it is stopped.
restart_prod() {
  stop prod
  backup_stopped_prod
  up prod
  wait_healthy prod "$PROD_PORT"
}

start_prod() {
  log "prod: switching to prod"
  if pull_changed prod; then log "new prod image"; fi
  if running qa; then
    stop qa
    log "stopped qa"
  fi
  restart_prod
}

start_qa() {
  log "qa: switching to qa"
  if pull_changed qa; then log "new qa image"; fi
  if running prod; then
    stop prod
    log "stopped prod"
  fi
  stop qa
  if [ -f "prod/$DB" ]; then
    copy_db prod "qa/$DB"
    log "copied prod's data to qa"
  fi
  up qa
  wait_healthy qa "$QA_PORT"
}

update() {
  log "update: checking for new images"
  changed=0
  # A stopped environment only downloads; it starts on the new image next time.
  if pull_changed prod; then
    changed=1
    if running prod; then
      log "new prod image"
      restart_prod
    else
      log "new prod image downloaded; prod starts on it next time"
    fi
  fi
  if pull_changed qa; then
    changed=1
    if running qa; then
      log "new qa image"
      up qa
      wait_healthy qa "$QA_PORT"
    else
      log "new qa image downloaded; qa starts on it next time"
    fi
  fi
  # Remove superseded Metis images only, never another app's.
  "$DOCKER" image prune -f --filter "label=org.opencontainers.image.title=metis" >/dev/null 2>&1 || true
  if [ "$PULL_FAILED" -eq 1 ]; then
    log "update: finished with errors"
  elif [ "$changed" -eq 0 ]; then
    log "update: nothing new"
  fi
}

backup() {
  log "backup: starting"
  if running prod; then
    restart_prod
  else
    backup_stopped_prod
  fi
}

case "$CMD" in
  prod | qa | update | backup)
    # A command that stops early says so in the log, with its exit code.
    trap 'rc=$?; [ "$rc" -eq 0 ] || log "ERROR: $CMD failed (exit $rc)"' EXIT
    ;;
esac

case "$CMD" in
  qa) start_qa ;;
  prod) start_prod ;;
  update) update ;;
  backup) backup ;;
  status) compose ps ;;
  *)
    echo "usage: $0 prod | qa | update | backup | status" >&2
    exit 2
    ;;
esac
