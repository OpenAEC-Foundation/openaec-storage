#!/usr/bin/env bash
# Draai TLC op één model: ./tlc.sh <Module> <cfg> [extra TLC-opties]
# Grenzen: geheugen -Xmx (standaard 3g, via TLC_XMX) en tijd (standaard 2 uur, via TLC_TIJD).
# Op aia gaat elke run via de rij voor zware runs (ZWAAR, standaard /home/aia09/worktrees/zwaar.sh): hoogstens twee
# zware runs tegelijk voor alle threads samen. De tijdgrens telt pas vanaf de start, niet tijdens het wachten.
# De klasse tlc2.TLC staat niet letterlijk in de opdracht voor zwaar.sh: dat script telt elke opdrachtregel met
# "tlc2.TLC" als draaiende TLC, ook een die nog in de rij wacht.
set -u
module="$1"; cfg="$2"; shift 2
JAVA="${JAVA:-$HOME/opt/jdk-21.0.13+9/bin/java}"
TLA2TOOLS="${TLA2TOOLS:-$HOME/opt/tla2tools.jar}"
ZWAAR="${ZWAAR-/home/aia09/worktrees/zwaar.sh}"
rij=()
[ -n "$ZWAAR" ] && [ -x "$ZWAAR" ] && rij=("$ZWAAR")
mkdir -p runs
log="runs/$(basename "$cfg" .cfg).log"
meta="$(mktemp -d)"
export TLC_KLASSE="tlc2.TLC"
"${rij[@]}" bash -c 'exec timeout "$1" nice -n 10 "$2" -Xmx"$3" -XX:+UseParallelGC -cp "$4" "$TLC_KLASSE" "${@:5}"' \
    _ "${TLC_TIJD:-7200}" "$JAVA" "${TLC_XMX:-3g}" "$TLA2TOOLS" \
    -config "$cfg" -workers "${TLC_WORKERS:-2}" -metadir "$meta" -deadlock "$@" "$module" > "$log" 2>&1
code=$?
rm -rf "$meta"
echo "EXIT:$code" >> "$log"
tail -n 25 "$log"
exit "$code"
