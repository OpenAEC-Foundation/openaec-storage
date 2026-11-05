#!/usr/bin/env bash
# Draait TLC-modellen na elkaar, nooit tegelijk met een andere TLC (afspraak op aia: hoogstens twee zware runs,
# en een TLC telt als één). Gebruik: ./draai_reeks.sh <Module> <cfg-naam> [<cfg-naam> ...]
# Per model een regel in runs/samenvatting.txt: naam, uitkomst, aantal toestanden, duur.
set -u
cd "$(dirname "$0")"
module="$1"; shift
for naam in "$@"; do
  while pgrep -f 'tlc2[.]TLC|tla2tools[.]jar' >/dev/null; do sleep 15; done
  begin=$(date +%s)
  ./tlc.sh "$module" "$naam.cfg" ${TLC_EXTRA:-} > /dev/null
  duur=$(( $(date +%s) - begin ))
  log="runs/$naam.log"
  uitkomst=$(grep -E 'is violated|No error has been found|Temporal properties were violated' "$log" | head -1)
  [ -z "$uitkomst" ] && uitkomst="geen uitkomst ($(tail -1 "$log"))"
  toestanden=$(grep -E 'distinct states found' "$log" | tail -1 | sed -E 's/.* ([0-9.]+) distinct states found.*/\1/')
  printf '%s | %s | %s toestanden | %ss | %s\n' "$naam" "$uitkomst" "$toestanden" "$duur" "$(tail -1 "$log")" >> runs/samenvatting.txt
  rm -f ./*_TTrace_*
  # Een fout in de spec (geen schending en geen tijdgrens): stop, want de volgende modellen falen net zo.
  code=$(tail -1 "$log" | sed -n 's/^EXIT://p')
  case "$code" in 0|10|11|12|13|124) ;; *) echo "gestopt na $naam (EXIT:$code)" >> runs/samenvatting.txt; exit 1;; esac
done
