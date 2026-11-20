#!/usr/bin/env bash
# Draai TLC op één model: ./tlc.sh <Module> <cfg> [extra TLC-opties]
# Grenzen: geheugen -Xmx (standaard 3g, via TLC_XMX) en tijd (standaard 2 uur, via TLC_TIJD).
# Op aia gaat elke run via de rij voor zware runs (ZWAAR, standaard /home/aia09/worktrees/zwaar.sh): hoogstens twee
# zware runs tegelijk voor alle threads samen. De tijdgrens telt pas vanaf de start, niet tijdens het wachten.
# zwaar.sh telt elke opdrachtregel met "tlc2.TLC" als een TLC buiten de rij (en biedt anderen dan alleen plek 1
# aan). Deze runs gaan zelf door de rij en houden al een plek; ze zouden dan dubbel tellen, en met plek 1 de hele rij
# stilzetten. Daarom start TLC hier met `java -jar tla2tools.jar` (hoofdklasse tlc2.TLC): de rij telt de run via
# zijn plek, één keer. Zonder rij (ZWAAR=) maakt het niets uit.
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
"${rij[@]}" bash -c 'exec timeout "$1" nice -n 10 "$2" -Xmx"$3" -XX:+UseParallelGC -jar "$4" "${@:5}"' \
    _ "${TLC_TIJD:-7200}" "$JAVA" "${TLC_XMX:-3g}" "$TLA2TOOLS" \
    -config "$cfg" -workers "${TLC_WORKERS:-2}" -metadir "$meta" -deadlock "$@" "$module" > "$log" 2>&1
code=$?
rm -rf "$meta"
echo "EXIT:$code" >> "$log"
tail -n 25 "$log"
exit "$code"
