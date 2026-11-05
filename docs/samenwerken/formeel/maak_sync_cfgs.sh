#!/usr/bin/env bash
# Maakt de TLC-modellen voor SyncClient.tla. Eén regel per proef:
# naam | client | clients (2/3) | paden (1, 2, NietIfc) | atomen | verwijderingen | storingen | AppBuffer | fixes | spec | eigenschappen
set -eu
cd "$(dirname "$0")"
VEIL='INVARIANTS\n    GeenVernietiging\n    GeenStilVerlies\nPROPERTIES\n    GeenLostUpdate'
LEVEND='PROPERTIES\n    Convergentie'
while IFS='|' read -r naam client k paden a v s app fixes spec eig sym; do
  [ -z "$naam" ] && continue
  case "$eig" in veilig) blok="$VEIL";; levend) blok="$LEVEND";; esac
  symregel=""; [ "$sym" = "sym" ] && symregel="SYMMETRY Symmetrie$k"
  {
    echo "\\* SyncClient: $naam (client $client, $k clients, paden $paden, $a atomen, $v verwijderingen, $s storingen, AppBuffer $app, fixes {$fixes})"
    echo "SPECIFICATION $spec"
    echo "CONSTANTS"
    echo "    Clients <- MCClients$k"
    echo "    Paden <- MCPaden$paden"
    echo "    IfcPaden <- MCIfc"
    echo "    Bestanden <- MCBestanden"
    echo "    Client = \"$client\""
    echo "    MaxAtomen = $a"
    echo "    MaxVerwijder = $v"
    echo "    MaxStoring = $s"
    echo "    MaxPogingen = 5"
    echo "    AppBuffer = $app"
    echo "    Fixes = {$fixes}"
    echo "    Geen = Geen"
    [ -n "$symregel" ] && echo "$symregel"
    printf "$blok\n"
  } > "Sync_$naam.cfg"
done <<'LIJST'
main_k2p1|main|2|1|2|0|0|FALSE||Spec|veilig|
main_vervangen_k2p1|main|2|1|3|0|0|FALSE|"veilig_vervangen"|Spec|veilig|
main_vervangen_levend|main|2|1|3|0|0|FALSE|"veilig_vervangen"|FairSpec|levend|
main_kenmerk_k2p1|main|2|1|2|0|0|FALSE|"kenmerk_voor"|Spec|veilig|
5a_k2p1|5a|2|1|2|1|1|FALSE||Spec|veilig|
5a_k2p1_fix|5a|2|1|2|1|1|FALSE|"verwijder_basis"|Spec|veilig|
5b_k2p1|5b|2|1|2|1|1|FALSE||Spec|veilig|
5b_k2p1_fix|5b|2|1|2|1|1|FALSE|"verwijder_basis"|Spec|veilig|
5b_nietifc_a3|5b|2|NietIfc|3|1|0|FALSE|"verwijder_basis"|Spec|veilig|
5b_nietifc_a3_fix|5b|2|NietIfc|3|1|0|FALSE|"verwijder_basis","upload_bestand"|Spec|veilig|
5b_ifc_a3_fix|5b|2|1|3|1|1|FALSE|"verwijder_basis","upload_bestand"|Spec|veilig|
5b_appbuffer|5b|2|1|2|0|0|TRUE|"verwijder_basis","upload_bestand"|Spec|veilig|
5a_levend_fix|5a|2|1|2|1|1|FALSE|"verwijder_basis","upload_bestand"|FairSpec|levend|
5b_levend_fix|5b|2|1|2|1|1|FALSE|"verwijder_basis","upload_bestand"|FairSpec|levend|
5b_k3p1_fix|5b|3|1|3|1|1|FALSE|"verwijder_basis","upload_bestand"|Spec|veilig|sym
5b_k3p2_fix|5b|3|2|3|1|1|FALSE|"verwijder_basis","upload_bestand"|Spec|veilig|sym
5b_k3p2_nu|5b|3|2|3|1|1|FALSE||Spec|veilig|sym
LIJST
ls Sync_*.cfg | wc -l
