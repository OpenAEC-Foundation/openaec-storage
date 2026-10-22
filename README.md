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
| `openaec_storage_prefix` | Voorvoegsel voor nieuwe S3-sleutels, bijv. de host van de klantsite, zodat meerdere klanten één bucket delen (optioneel) |
| `openaec_storage_endpoint` | Alternatief S3-adres voor de server zelf (optioneel, bijv. een S3-compatibele testopslag) |
| `openaec_storage_endpoint_publiek` | Adres in ondertekende links voor browser en sync-client, als dat afwijkt van het serveradres (optioneel) |
| `openaec_storage_kms_key` | KMS-sleutel van de klant (optioneel; anders standaardversleuteling van de bucket) |
| `openaec_storage_quotum_gb` | Quotum van de omgeving in GB (gezet door de regelkamer; 0 = onbeperkt) |
| `openaec_storage_ruimte_makers` | Rollen die een nieuwe ruimte mogen aanmaken (lijst; standaard `System Manager`, `OpenAEC Eigenaar` en `OpenAEC Beheerder`). Administrator en System Manager mogen het altijd |

## Rollen per ruimte

`beheerder` (leden beheren, alles), `schrijver` (lezen, uploaden, verplaatsen, verwijderen), `lezer` (lezen),
`gast` (lezen). Administrator en System Manager hebben overal toegang.

Leden en rollen van een bestaande ruimte wijzigt alleen een `beheerder` van die ruimte, langs elke weg: het formulier,
`/api/resource` (ook een los `Ruimtelid`), `frappe.client.insert` en `frappe.client.set_value`. Een schrijver, lezer
of gast kan zichzelf of een ander dus niet toevoegen en geen hogere rol geven.

## Ruimtes aanmaken

Een nieuwe ruimte aanmaken mag alleen een beheerder van de omgeving: Administrator, System Manager en de beheerrollen
`OpenAEC Eigenaar` en `OpenAEC Beheerder` (uit openaec_beheer; zonder die app blijven Administrator en System Manager
over). Wie de ruimte aanmaakt, wordt er `beheerder` van. Beheerder van een ruimte zijn is niet genoeg: anderen krijgen
de melding "Alleen een beheerder van de omgeving kan een ruimte aanmaken." Een andere lijst rollen:

    bench --site <site> set-config -p openaec_storage_ruimte_makers '["OpenAEC Eigenaar", "Projectleider"]'

Heeft iemand op `/opslag` nog geen enkele ruimte, dan ziet een beheerder van de omgeving de knop "Ruimte aanmaken in
Beheer" (naar `/beheer/opslag`) en een ander de vraag om zijn beheerder hem toe te voegen. Welke van de twee bepaalt
de server met dezelfde regel als bij het aanmaken.

## Tests

    bench --site <site> run-tests --app openaec_storage

De tests gebruiken een nagebootste S3 (moto, `pip install -e apps/openaec_storage[test]`); er is geen echte bucket
nodig. Een eigen opslagadres in de site_config (bijv. een S3-compatibele testopslag) staat tijdens die tests uit.

## WebDAV-gateway (`gateway/`)

Rust-dienst (`openaec-dav`) die Storage als WebDAV aanbiedt, zodat je een ruimte als netwerkschijf koppelt of
bestanden opent vanuit programma's die WebDAV spreken. De gateway heeft geen eigen opslag of rechten: elke
handeling gaat via de API hierboven met de inlog van de gebruiker.

- Paden: `/<ruimte>/<map>/…/<bestand>`.
- Inloggen: Basic-auth met API-sleutel als gebruikersnaam en API-geheim als wachtwoord (later: app-wachtwoorden
  uit de beheeromgeving); een bearer-token wordt doorgegeven.
- Schrijven gaat als (delta-)upload met de huidige versie als basis: overschrijven wordt een nieuwe versie, of een
  conflictkopie als een ander het bestand intussen wijzigde of vergrendelde. Opslaan via een tijdelijk bestand en
  `MOVE` (of `COPY`) over het origineel wordt eveneens een nieuwe versie van hetzelfde bestand.
- Instellingen: `OPENAEC_BASIS` (adres van de klantomgeving) en `OPENAEC_DAV_ADRES` (standaard `0.0.0.0:8310`).

```bash
cd gateway && cargo test
bash gateway/build-context.sh /tmp/dav-ctx && docker build -f /tmp/dav-ctx/Containerfile -t openaec-dav /tmp/dav-ctx
```
