#!/bin/sh
set -e

# Run as PUID:PGID so files in the data volume belong to a host user. The
# defaults are Unraid's nobody:users, which owns everything under appdata.
if [ "$(id -u)" = "0" ]; then
    PUID="${PUID:-99}"
    PGID="${PGID:-100}"
    mkdir -p "$THERMOHUB_DATA_DIR"
    chown -R "$PUID:$PGID" "$THERMOHUB_DATA_DIR"
    # Chromium needs a writable home for its caches; root's isn't.
    export HOME=/tmp/thermohub
    mkdir -p "$HOME"
    chown "$PUID:$PGID" "$HOME"
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

exec "$@"
