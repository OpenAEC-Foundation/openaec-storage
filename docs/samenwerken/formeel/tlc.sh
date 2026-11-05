#!/usr/bin/env bash
# Draai TLC op één model: ./tlc.sh <Module> <cfg> [extra TLC-opties]
# Grenzen: geheugen -Xmx (standaard 3g, via TLC_XMX) en tijd (standaard 2 uur, via TLC_TIJD).
set -u
module="$1"; cfg="$2"; shift 2
JAVA="${JAVA:-$HOME/opt/jdk-21.0.13+9/bin/java}"
TLA2TOOLS="${TLA2TOOLS:-$HOME/opt/tla2tools.jar}"
mkdir -p runs
log="runs/$(basename "$cfg" .cfg).log"
meta="$(mktemp -d)"
timeout "${TLC_TIJD:-7200}" nice -n 10 "$JAVA" -Xmx"${TLC_XMX:-3g}" -XX:+UseParallelGC -cp "$TLA2TOOLS" tlc2.TLC \
    -config "$cfg" -workers "${TLC_WORKERS:-2}" -metadir "$meta" -deadlock "$@" "$module" > "$log" 2>&1
code=$?
rm -rf "$meta"
echo "EXIT:$code" >> "$log"
tail -n 25 "$log"
