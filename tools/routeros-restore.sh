#!/usr/bin/env bash
# Restore a binary backup made by routeros-backup.sh onto a MikroTik RouterOS 7 router.
#
# The router REBOOTS with the restored configuration: the network is down for about a
# minute, and the router's IP, users and passwords become those in the backup.
# Only restore onto the same model the backup came from (see router-info.txt).
#
# The .rsc export is not restored by this script: it has no user passwords or
# certificate keys, so it is for reading and manual rebuilds only.
#
# Usage: routeros-restore.sh [-r router] [-u ssh-user] <backup-dir>
set -euo pipefail

ROUTER=192.168.88.1
SSH_USER="admin"

usage() { sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p;}' "$0"; exit "${1:-0}"; }
while getopts "r:u:h" opt; do
  case $opt in
    r) ROUTER=$OPTARG ;;
    u) SSH_USER=$OPTARG ;;
    h) usage 0 ;;
    *) usage 1 ;;
  esac
done
shift $((OPTIND - 1))
[[ $# -eq 1 ]] || usage 1
DIR=$1

shopt -s nullglob
backups=("$DIR"/*.backup)
[[ ${#backups[@]} -eq 1 ]] || { echo "Expected exactly one .backup file in $DIR" >&2; exit 1; }
FILE=${backups[0]}
NAME=$(basename "$FILE")

sha256c() { if command -v sha256sum >/dev/null; then sha256sum -c "$@"; else shasum -a 256 -c "$@"; fi; }
echo "Verifying checksums..."
(cd "$DIR" && sha256c --status SHA256SUMS) || { echo "Checksum mismatch: backup is corrupt or modified" >&2; exit 1; }

TMP=$(mktemp -d)
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$TMP/cm" -o ControlPersist=60 -o ConnectTimeout=10)
# shellcheck disable=SC2029 # RouterOS commands are built locally on purpose
ros() { ssh "${SSH_OPTS[@]}" "$SSH_USER@$ROUTER" "$1"; }
upload() {
  scp -q "${SSH_OPTS[@]}" "$1" "$SSH_USER@$ROUTER:$2" 2>/dev/null \
    || scp -q -O "${SSH_OPTS[@]}" "$1" "$SSH_USER@$ROUTER:$2"
}
uploaded=false
cleanup() {
  if $uploaded; then  # only reached if the restore didn't happen (it reboots otherwise)
    ros "/file remove [find where name=\"$NAME\"]" 2>/dev/null \
      || echo "WARNING: could not delete $NAME from the router; remove it by hand" >&2
  fi
  ssh "${SSH_OPTS[@]}" -O exit "$SSH_USER@$ROUTER" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

model() { tr -d '\r' | sed -n 's/^ *model: *//p' | head -1; }
backup_model=$(model <"$DIR/router-info.txt")
router_model=$(ros "/system routerboard print" | model)
echo "Backup from:   $backup_model ($(tr -d '\r' <"$DIR/router-info.txt" | sed -n 's/^ *version: *//p' | head -1))"
echo "Target router: $router_model at $ROUTER"
if [[ $backup_model != "$router_model" ]]; then
  echo "Model mismatch: binary backups only restore onto the same model. Aborting." >&2
  exit 1
fi

echo
echo "This will restore $NAME and REBOOT the router. Type RESTORE to continue."
read -r answer
[[ $answer == RESTORE ]] || { echo "Aborted."; exit 1; }
read -rsp "Backup encryption password: " PW; echo
[[ $PW =~ ^[A-Za-z0-9._+=@%^:,-]+$ ]] || { echo "Password has unsupported characters" >&2; exit 1; }

echo "Uploading..."
upload "$FILE" "$NAME"
uploaded=true

echo "Restoring. RouterOS asks for a final confirmation; answer y to reboot."
# -t: the router's confirmation prompt needs a terminal. The connection drops when it reboots.
ssh -t "${SSH_OPTS[@]}" "$SSH_USER@$ROUTER" "/system backup load name=$NAME password=$PW" || true
ssh "${SSH_OPTS[@]}" -O exit "$SSH_USER@$ROUTER" 2>/dev/null || true  # fresh connections from here

echo "Waiting for the router to go down..."
rebooting=false
for _ in $(seq 1 12); do
  if ! ssh -o ConnectTimeout=3 -o ControlMaster=no "$SSH_USER@$ROUTER" ":put ok" >/dev/null 2>&1; then
    rebooting=true
    break
  fi
  sleep 5
done
if ! $rebooting; then
  echo "Router is still up after 60 s: restore declined or failed (wrong password?)." >&2
  exit 1
fi
uploaded=false  # the router is rebooting; nothing left to clean up
echo "Router is rebooting with the restored configuration. Give it about a minute."
echo "The uploaded .backup file stays on the router: delete it afterwards with"
echo "  /file remove [find where name=\"$NAME\"]"
