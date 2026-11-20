#!/usr/bin/env bash
# Maakt de TLC-modellen voor SyncClient.tla. Eén regel per proef:
# naam | client | clients (2/3) | paden (1, 2, NietIfc) | kopiepaden | atomen | verwijderingen | storingen |
#   AppBuffer | OpenBestand | fixes | spec | eigenschappen | symmetrie
set -eu
cd "$(dirname "$0")"
rm -f Sync_*.cfg
VEIL='INVARIANTS\n    GeenVernietiging\n    GeenStilVerlies\nPROPERTIES\n    GeenLostUpdate'
LEVEND='PROPERTIES\n    Convergentie'
while IFS='|' read -r naam client k paden kopie a v s app open fixes spec eig sym; do
  [ -z "$naam" ] && continue
  case "$eig" in veilig) blok="$VEIL";; levend) blok="$LEVEND";; esac
  {
    echo "\\* SyncClient: $naam (client $client, $k clients, paden $paden, $kopie kopiepaden, $a atomen, $v verwijderingen, $s storingen, AppBuffer $app, OpenBestand $open, fixes {$fixes})"
    echo "SPECIFICATION $spec"
    echo "CONSTANTS"
    echo "    Clients <- MCClients$k"
    echo "    Paden <- MCPaden$paden"
    echo "    KopiePaden <- MCKopie$kopie"
    echo "    IfcPaden <- MCIfc"
    echo "    Bestanden <- MCBestanden"
    echo "    Client = \"$client\""
    echo "    MaxAtomen = $a"
    echo "    MaxVerwijder = $v"
    echo "    MaxStoring = $s"
    echo "    MaxPogingen = 5"
    echo "    AppBuffer = $app"
    echo "    OpenBestand = $open"
    echo "    Fixes = {$fixes}"
    echo "    Geen = Geen"
    [ "$sym" = "sym" ] && echo "SYMMETRY Symmetrie$k"
    printf "$blok\n"
  } > "Sync_$naam.cfg"
done <<'LIJST'
main_k2p1|main|2|1|2|2|0|0|FALSE|FALSE||Spec|veilig|
main_kenmerk_k2p1|main|2|1|2|2|0|0|FALSE|FALSE|"kenmerk_voor"|Spec|veilig|
main_vervangen_k2p1|main|2|1|2|3|0|0|FALSE|FALSE|"veilig_vervangen"|Spec|veilig|
main_verwijder_k2p1|main|2|1|2|2|1|1|FALSE|FALSE|"kenmerk_voor","veilig_vervangen"|Spec|veilig|
main_verwijder_k2p1_fix|main|2|1|2|2|1|1|FALSE|FALSE|"kenmerk_voor","veilig_vervangen","verwijder_basis"|Spec|veilig|
5a_k2p1|5a|2|1|2|2|1|1|FALSE|FALSE||Spec|veilig|
5a_k2p1_fix|5a|2|1|2|2|1|1|FALSE|FALSE|"verwijder_basis"|Spec|veilig|
5b_k2p1|5b|2|1|2|2|1|1|FALSE|FALSE||Spec|veilig|
5b_k2p1_fix|5b|2|1|2|2|1|1|FALSE|FALSE|"verwijder_basis"|Spec|veilig|
5b_nietifc_a3|5b|2|NietIfc|2|3|1|0|FALSE|FALSE|"verwijder_basis"|Spec|veilig|
5b_nietifc_a3_fix|5b|2|NietIfc|2|3|1|0|FALSE|FALSE|"verwijder_basis","upload_bestand"|Spec|veilig|
5b_ifc_a3_fix|5b|2|1|2|3|1|1|FALSE|FALSE|"verwijder_basis","upload_bestand"|Spec|veilig|
5a_open_fix|5a|2|1|2|3|0|1|FALSE|TRUE|"verwijder_basis","upload_bestand"|Spec|veilig|
5b_open_fix|5b|2|1|2|3|0|1|FALSE|TRUE|"verwijder_basis","upload_bestand"|Spec|veilig|
5b_appbuffer|5b|2|1|2|2|0|0|TRUE|FALSE|"verwijder_basis","upload_bestand"|Spec|veilig|
5a_levend_fix|5a|2|1|3|2|1|1|FALSE|FALSE|"verwijder_basis","upload_bestand"|FairSpec|levend|
5b_levend_fix|5b|2|1|3|2|1|1|FALSE|FALSE|"verwijder_basis","upload_bestand"|FairSpec|levend|
5b_k3p1_fix|5b|3|1|2|3|1|1|FALSE|FALSE|"verwijder_basis","upload_bestand"|Spec|veilig|sym
5b_k3p2_fix|5b|3|2|2|3|1|1|FALSE|FALSE|"verwijder_basis","upload_bestand"|Spec|veilig|sym
5b_k3p2_nu|5b|3|2|2|3|1|1|FALSE|FALSE||Spec|veilig|sym
LIJST
ls Sync_*.cfg | wc -l
