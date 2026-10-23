#!/usr/bin/env bash
# Meting van de desktop-sync tegen een lokale bench: frappe/erpnext:v16.51.1 met mariadb, valkey en moto als S3,
# met deze werkkopie van openaec_storage. Vergelijkt twee builds van het meetprogramma van de crate openaec-sync
# (`cargo build --release --example meting`), bijv. een oudere versie (sequentieel) en de huidige (parallel en in
# groepen).
#
#   scripts/meetbench/meet.sh <meting oud> <meting nieuw> [werkmap]      zet de bench op, meet en ruimt alles op
#
# Gemeten: 2000 bestanden van 1–20 KB in 40 mappen, en 5 bestanden van 50 MB; per versie de upload naar een lege
# ruimte en de download naar een lege map (tijd van één sync-ronde). MEETBENCH_LATEN=1 laat de containers staan.
set -euo pipefail
cd "$(dirname "$0")"
OUD=$1
NIEUW=$2
WERK=${3:-$(mktemp -d)}
export MSYS_NO_PATHCONV=1
BASIS=http://127.0.0.1:18000

opruimen() {
	if [ -z "${MEETBENCH_LATEN:-}" ]; then
		docker compose down -v >/dev/null 2>&1 || true
	fi
}
trap opruimen EXIT

docker compose up -d >/dev/null
docker compose cp inrichten.sh bench:/tmp/inrichten.sh >/dev/null
docker compose cp serve.sh bench:/tmp/serve.sh >/dev/null
docker compose cp ruimtes.py bench:/home/frappe/frappe-bench/sites/ruimtes.py >/dev/null
docker compose exec -T bench bash /tmp/inrichten.sh >/dev/null
docker compose exec -T bench bash /tmp/serve.sh >/dev/null
UIT=$(docker compose exec -T -w /home/frappe/frappe-bench/sites bench ../env/bin/python ruimtes.py \
	oud-klein oud-groot nieuw-klein nieuw-groot 2>/dev/null)
export OPENAEC_AUTORISATIE=$(echo "$UIT" | sed -n 's/^sleutel //p')
ruimte() { echo "$UIT" | sed -n "s/^$1 //p" | tr -d '\r'; }

"$NIEUW" maak "$WERK/klein" 2000 0 >/dev/null
"$NIEUW" maak "$WERK/groot" 0 5 50 >/dev/null

for versie in oud nieuw; do
	programma=$OUD
	[ "$versie" = nieuw ] && programma=$NIEUW
	for set in klein groot; do
		rm -rf "$WERK/$set/.openaec" "$WERK/terug-$versie-$set"
		echo "== $versie, $set"
		"$programma" upload "$BASIS" "$(ruimte "$versie-$set")" "$WERK/$set"
		"$programma" download "$BASIS" "$(ruimte "$versie-$set")" "$WERK/terug-$versie-$set"
		diff -rq -x .openaec "$WERK/$set" "$WERK/terug-$versie-$set" >/dev/null && echo "   inhoud gelijk"
	done
done
