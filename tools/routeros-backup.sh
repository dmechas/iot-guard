#!/usr/bin/env bash
# Back up a MikroTik RouterOS 7 router over SSH, from your own machine.
#
# Produces, in <out-dir>/<timestamp>/:
#   <name>.backup  encrypted binary backup: the complete config incl. user passwords and
#                  certificate keys. Restore with routeros-restore.sh (same model only).
#   <name>.rsc     text export (show-sensitive): readable and diffable, for reference and
#                  manual rebuilds. Contains WiFi/PPPoE secrets, but no user passwords
#                  or certificate keys, so it can't fully restore the router on its own.
#   router-info.txt  identity, model, RouterOS version
#   SHA256SUMS
#
# Usage: routeros-backup.sh [-r router] [-u ssh-user] [-o out-dir]
# Needs an SSH user with full rights on the router (e.g. admin). Use an SSH key
# (/user ssh-keys import) to avoid password prompts.
set -euo pipefail

ROUTER=192.168.88.1
SSH_USER="admin"
OUT_ROOT=./routeros-backups

usage() { sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p;}' "$0"; exit "${1:-0}"; }
while getopts "r:u:o:h" opt; do
  case $opt in
    r) ROUTER=$OPTARG ;;
    u) SSH_USER=$OPTARG ;;
    o) OUT_ROOT=$OPTARG ;;
    h) usage 0 ;;
    *) usage 1 ;;
  esac
done

TS=$(date +%Y%m%d-%H%M%S)
NAME="iotg-backup-$TS"
DEST="$OUT_ROOT/$TS"
TMP=$(mktemp -d)
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$TMP/cm" -o ControlPersist=60 -o ConnectTimeout=10)

# shellcheck disable=SC2029 # RouterOS commands are built locally on purpose
ros() { ssh "${SSH_OPTS[@]}" "$SSH_USER@$ROUTER" "$1"; }
fetch() {  # newer OpenSSH scp uses SFTP; fall back to the legacy protocol if that fails
  scp -q "${SSH_OPTS[@]}" "$SSH_USER@$ROUTER:$1" "$2" 2>/dev/null \
    || scp -q -O "${SSH_OPTS[@]}" "$SSH_USER@$ROUTER:$1" "$2"
}
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

created_on_router=false
cleanup() {
  # The files on the router contain secrets: always remove them.
  if $created_on_router; then
    ros "/file remove [find where name=\"$NAME.backup\" or name=\"$NAME.rsc\"]" \
      || echo "WARNING: could not delete $NAME.* from the router; remove them by hand" >&2
  fi
  ssh "${SSH_OPTS[@]}" -O exit "$SSH_USER@$ROUTER" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

read -rsp "Encryption password for the binary backup: " PW; echo
read -rsp "Repeat password: " PW2; echo
[[ $PW == "$PW2" ]] || { echo "Passwords don't match" >&2; exit 1; }
# The password is embedded in a RouterOS command, so keep it to characters that need no quoting.
[[ ${#PW} -ge 12 && $PW =~ ^[A-Za-z0-9._+=@%^:,-]+$ ]] \
  || { echo "Password must be 12+ characters of A-Z a-z 0-9 . _ + = @ % ^ : , -" >&2; exit 1; }
echo "Store this password safely: the .backup is useless without it."

umask 077
mkdir -p "$DEST"

echo "Reading router info..."
# identity print wraps the name one character per line without a terminal, so read it with get
ros ":local n [/system identity get name]; :put \"identity: \$n\"; /system resource print; /system routerboard print" \
  >"$DEST/router-info.txt"

echo "Creating backup and export on $ROUTER..."
created_on_router=true
ros "/system backup save name=$NAME password=$PW" >/dev/null
ros "/export show-sensitive file=$NAME" >/dev/null

for _ in $(seq 1 15); do  # the export file can take a moment to appear
  [[ $(ros ":put [/file print count-only where name=\"$NAME.rsc\"]" | tr -d '\r') == 1 ]] && break
  sleep 1
done

echo "Downloading..."
fetch "$NAME.backup" "$DEST/"
fetch "$NAME.rsc" "$DEST/"
[[ -s $DEST/$NAME.backup && -s $DEST/$NAME.rsc ]] || { echo "Download incomplete" >&2; exit 1; }

(cd "$DEST" && sha256 "$NAME.backup" "$NAME.rsc" router-info.txt >SHA256SUMS)
echo "Done: $DEST"
ls -l "$DEST"
