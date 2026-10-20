# OpenAEC Storage

Frappe-app voor projectbestanden in de OpenAEC SuperCloud: ruimtes met leden en rollen, mappen, bestanden met
versies, prullenbak en een wijzigingenlijst voor de desktop-sync-client. De bestanden zelf staan in S3; Frappe
houdt alleen de administratie bij en geeft tijdelijke, ondertekende links uit.

Ontwerp: `openaec-supercloud/docs/superpowers/specs/2026-10-20-openaec-storage-design.md` (fase 4a).

## Instellingen (site_config.json)

| Sleutel | Betekenis |
|---|---|
| `openaec_storage_bucket` | S3-bucket van deze omgeving (verplicht) |
| `openaec_storage_region` | S3-regio (standaard `eu-central-1`) |
| `openaec_storage_endpoint` | Alternatief S3-adres voor de server zelf (optioneel, bijv. een S3-compatibele testopslag) |
| `openaec_storage_endpoint_publiek` | Adres in ondertekende links voor browser en sync-client, als dat afwijkt van het serveradres (optioneel) |
| `openaec_storage_kms_key` | KMS-sleutel van de klant (optioneel; anders standaardversleuteling van de bucket) |
| `openaec_storage_quotum_gb` | Quotum van de omgeving in GB (gezet door de regelkamer; 0 = onbeperkt) |

## Rollen per ruimte

`beheerder` (leden beheren, alles), `schrijver` (lezen, uploaden, verplaatsen, verwijderen), `lezer` (lezen),
`gast` (lezen). Administrator en System Manager hebben overal toegang.

## Tests

    bench --site <site> run-tests --app openaec_storage

De tests gebruiken een nagebootste S3 (moto); er is geen echte bucket nodig.
