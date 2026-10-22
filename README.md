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
| `openaec_storage_overdracht_max_gb` | Grootste overdracht in GB, alle bestanden samen (standaard 100) |
| `openaec_storage_overdracht_max_dagen` | Langste vervaltermijn van een overdracht in dagen (standaard 30; de standaardtermijn is 7) |
| `openaec_storage_overdracht_max_ontvangers` | Hoogste aantal e-mailontvangers per overdracht (standaard 20) |
| `openaec_storage_overdracht_concept_uren` | Hoe lang een niet-voltooide overdracht blijft staan voor hij wordt opgeruimd (standaard 24) |
| `openaec_storage_overdracht_zip_max_gb` | Tot deze grootte is "Alles downloaden" één zip; daarboven per bestand (standaard 2) |
| `openaec_storage_overdracht_rollen` | Extra rollen die overdrachten mogen maken (lijst; optioneel, zie Overdrachten) |

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

## Overdrachten

Losse bestanden versturen via een link, eventueel met een e-mail aan de ontvangers ("Versturen met SuperCloud
Storage" in de desktop-app). De bestanden hoeven niet in een ruimte te staan en komen er ook niet in: ze staan in S3
onder `<openaec_storage_prefix>/overdrachten/<overdracht>/`. Zolang een overdracht bestaat (concept of actief) telt
hij mee in het opslaggebruik van de omgeving (`api.quotum`, ook als `overdrachten_bytes`) en geldt het quotum.

Wie mag: wie kan delen, dus schrijver of beheerder in een ruimte, een beheerder van de omgeving, of een rol uit
`openaec_storage_overdracht_rollen`. Gasten niet. Een overdracht ziet en beheert alleen de maker (en het beheer).

Statussen: `Concept` (bestanden worden geüpload) → `Actief` (link werkt) → `Verlopen` (vervaldatum voorbij, of een
concept dat langer dan `openaec_storage_overdracht_concept_uren` bleef liggen) of `Ingetrokken`. De geplande taak
`openaec_storage.overdracht.opruimen` (elk uur) haalt bij Verlopen de objecten uit S3; bij intrekken gebeurt dat
meteen.

### API voor de desktop-app

Alle methodes onder `/api/method/openaec_storage.overdracht.<methode>`, met de inlog van de gebruiker (OAuth-token).
Methodes die iets veranderen alleen met POST. Antwoorden staan zoals altijd bij Frappe in `message`. Tijden zijn
`JJJJ-MM-DD UU:MM:SS` in de tijdzone van de site; groottes in bytes (GB = 10^9).

| Methode | Parameters | Antwoord |
|---|---|---|
| `grenzen` (GET) | — | `{max_bytes, standaard_dagen, max_dagen, max_ontvangers, concept_uren, zip_max_bytes, quotum: {gebruikt_bytes, limiet_bytes, overdrachten_bytes}}` |
| `maak` | `naam`, `bericht?`, `verloopt_op?` (datum `JJJJ-MM-DD` = tot het eind van die dag, of datum-tijd; leeg = 7 dagen), `wachtwoord?`, `ontvangers?` (lijst of tekst met komma's/regels) | `{overdracht, naam, status: "Concept", verloopt_op, max_bytes, concept_geldig_tot}` |
| `bestand_start` | `overdracht`, `naam`, `grootte`, `sha256?` | `{bestand, naam, grootte, deelgrootte, delen: [{nummer, url}]}` — `naam` krijgt ` (2)` als hij al in de overdracht staat |
| `bestand_stand` (GET) | `overdracht`, `bestand` | `{bestand, naam, grootte, klaar, deelgrootte, aanwezig: [{nummer, grootte, etag}], ontbrekend: [nummer], delen: [{nummer, url}]}` — `delen` zijn nieuwe links voor de ontbrekende delen |
| `bestand_klaar` | `overdracht`, `bestand`, `delen?` (`[{nummer, etag}]`; mag weg) | `{bestand, naam, grootte, klaar: true}` |
| `bestand_weg` | `overdracht`, `bestand` | — (alleen in een concept) |
| `voltooi` | `overdracht` | `{overdracht, status: "Actief", token, url, verloopt_op, aantal_bestanden, totale_grootte, mail: [adressen waarnaar de mail in de wachtrij staat]}` |
| `info` (GET) | `overdracht` | `{overdracht, naam, status, bericht, ontvangers, verloopt_op, beveiligd, url, totale_grootte, aantal_downloads, concept_geldig_tot, bestanden: [{bestand, naam, grootte, klaar, aantal_downloads}]}` |
| `mijn` (GET) | — | `[{overdracht, naam, status, verloopt_op, totale_grootte, aantal_bestanden, aantal_downloads, laatst_gedownload, ontvangers, beveiligd, url, gemaakt, voltooid_op}]`, nieuwste eerst; `url` alleen bij Actief |
| `trek_in` | `overdracht` | `{overdracht, status: "Ingetrokken"}` |

Uploaden gaat net als bij gewone uploads: elk deel van `deelgrootte` bytes (8 MiB, groter boven 80 GiB) met een PUT
naar zijn `url` (geldig 15 minuten); de ETag uit het antwoord mag de app bewaren maar hoeft niet. Hervatten na een
onderbreking (of na het opnieuw starten van de app): `info` voor de bestanden die nog niet `klaar` zijn, per bestand
`bestand_stand`, de `delen` uit dat antwoord uploaden en `bestand_klaar` zonder `delen`. Zijn de links verlopen,
dan geeft `bestand_stand` nieuwe. Een concept blijft `concept_uren` staan (zie `concept_geldig_tot`); daarna is het
opgeruimd en moet de app opnieuw beginnen. Fouten komen als gewone Frappe-fouten: 403 (geen recht, niet jouw
overdracht), 404 (bestaat niet) of 417 met een leesbare melding (grens overschreden, nog niet alles geüpload).

### Ontvangstpagina

`https://<site>/overdracht/<token>`: dezelfde schil en huisstijl als de deelpagina, met naam, bericht, afzender,
vervaldatum en de bestanden met grootte. Per bestand downloaden gaat met een tijdelijke link rechtstreeks uit S3.
"Alles downloaden" is tot `openaec_storage_overdracht_zip_max_gb` één zip die de server streamt (zonder compressie
en zonder hem op te slaan); daarboven haalt de pagina de bestanden een voor een op, want een grote zip houdt een
werker van de server lang bezig en kan bij een haperende verbinding niet verder waar hij was, een losse download uit
S3 wel. Elke download (en elke zip) telt in `aantal_downloads`. Een wachtwoord vraagt de pagina eerst; een onbekende
link geeft 404, een verlopen of ingetrokken overdracht 410 met een nette melding.

Voor de pagina (gast, POST): `openen(token, wachtwoord?)` → `{naam, bericht, afzender, verloopt_op, totale_grootte,
zip, bestanden: [{bestand, naam, grootte}]}`, `download(token, bestand, wachtwoord?)` → `{url, geldig_seconden}` en
`zip(token, wachtwoord?)` → de zip zelf.

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
