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

## Ruimtebeheer

### Velden op Ruimte

| Veld | Soort | Betekenis |
|---|---|---|
| `gearchiveerd` | Check (alleen lezen) | De ruimte staat in het archief; alleen te wijzigen met `archiveer`/`herstel` |
| `gearchiveerd_op` | Datetime (alleen lezen) | Wanneer gearchiveerd (leeg na terugzetten) |
| `gearchiveerd_door` | Link User (alleen lezen) | Wie archiveerde (leeg na terugzetten) |
| `quotum_gb` | Float | Eigen quotum van de ruimte in GB; 0 of leeg = geen eigen limiet. Alleen een beheerder van de omgeving wijzigt het |
| `mapsjabloon` | Link Mapsjabloon (eenmalig) | Sjabloon waarmee de ruimte is aangemaakt |
| `mapregels` | Tabel Ruimtemapregel | Regels per map: `map` (Link Opslagmap), `pad`, `weergavenaam`, `volgorde`, `pictogram`, `extensies`, `alleen_lezen` |

### Archiveren

Een gearchiveerde ruimte is voor iedereen alleen te lezen, ook voor Administrator en de beheerders. Uploaden (ook
het afronden van een upload die al liep), verplaatsen, hernoemen, verwijderen, herstellen uit de prullenbak, mappen
maken, vergrendelen, een slot openbreken en delen geven 403 met de foutcode `ruimte_gearchiveerd`, langs elke weg die
de API gebruikt: browser, desktop-app, WebDAV en uploadlinks. Ook het formulier en `/api/resource` geven geen
schrijfrecht meer (daar zonder foutcode). Bladeren, downloaden, versies en de wijzigingenlijst blijven werken, en de
leden blijven te beheren. Wie geen lid is, krijgt de gewone melding "Geen toegang".

De ruimte staat niet meer in `api.ruimtes`, dus niet op `/opslag` en niet in de desktop-app; die stopt met syncen.
Het beheer ziet hem in `ruimtebeheer.lijst` (filter `gearchiveerd`) en in de lijst van Ruimte in de desk (filter
Gearchiveerd).

Deellinks naar een gearchiveerde ruimte werken niet, ook niet om te lezen of te downloaden: de deelpagina geeft 410
("Niet meer beschikbaar") en `delen.openen`, `download` en `upload_start` geven 403 met `ruimte_gearchiveerd`. Dat is
de veiligste keuze: een gearchiveerd project is af, en een link die maanden later nog werkt is een risico dat niemand
meer ziet. De links blijven bestaan en werken weer na terugzetten. `aantal_actieve_deellinks` (in `lijst`,
`archiveer` en `herstel`) telt de links die nu werken, ook uploadlinks: niet ingetrokken en niet verlopen, dezelfde
regel als bij het openen van een link. Daarmee kan het beheer vooraf waarschuwen hoeveel externe links stoppen. Gebruikersdelingen blijven lezen toestaan, net als
het lidmaatschap.

Archiveren en terugzetten mag een beheerder van de ruimte of een beheerder van de omgeving (zie Ruimtes aanmaken,
`openaec_storage_ruimte_makers`). Beide komen in de wijzigingenlijst (Opslagwijziging) met de soort `gearchiveerd`
of `teruggezet`, `doel_doctype` `Ruimte` en `doel` de ruimte.

### Quotum per ruimte

`quotum_gb` is een limiet binnen het quotum van de omgeving (`openaec_storage_quotum_gb`, de pool). Het gebruik van
een ruimte telt zoals de pool: alle versies van alle bestanden in de ruimte, ook oudere versies en de prullenbak
(niet de overdrachten, die horen bij geen ruimte). Een upload, ook via de desktop-app, WebDAV of een uploadlink,
moet in beide passen; anders 417 met de foutcode `quotum_pool_vol` of `quotum_ruimte_vol` (eerst wordt de pool
getoetst). De melding bevat het woord "quotum", zodat de WebDAV-gateway 507 Insufficient Storage geeft.

De quota van de ruimtes samen mogen groter zijn dan de pool (overboeking): niet elke ruimte zit tegelijk vol. Wat
het eerst vol is, de pool of de ruimte, weigert. `api.quotum` geeft de pool zoals altijd.

### Mapsjablonen

Een Mapsjabloon is een vaste mappenindeling voor een nieuwe ruimte, per klantsite te beheren in de desk of het
beheer (kiezen mag iedereen die is ingelogd, wijzigen alleen een beheerder van de omgeving).

| DocType | Velden |
|---|---|
| `Mapsjabloon` (naam = `naam`) | `naam`, `omschrijving`, `soort` (`team`, `project`, `bedrijf`), `standaard` (één per soort), `mappen` |
| `Mapsjabloonmap` (tabel) | `pad` (mapnamen met `/`, bijv. `Projecten/Lopend`), `weergavenaam` (standaard het laatste deel van het pad), `volgorde`, `pictogram` (naam uit @openaec/icons, bijv. `ui/document`), `extensies` (bijv. `pdf, dwg`; leeg = alles), `alleen_lezen` |

Een nieuwe ruimte met `mapsjabloon` krijgt bij het aanmaken alle mappen (ook tussenliggende) en een kopie van de
regels in `mapregels`; de nieuwe mappen staan in de wijzigingenlijst. Daarna verandert de ruimte niet mee als het
sjabloon wordt aangepast; een beheerder van de ruimte kan de regels op de ruimte zelf aanpassen. `standaard` is de
keuze die het beheer voorselecteert bij een nieuwe ruimte van die soort; zonder `mapsjabloon` legt de server niets
aan. Er is geen ruimtesoort `bedrijf`: een bedrijfsmap is een teamruimte, dus `sjablonen(soort="team")` geeft ook de
sjablonen van soort `bedrijf`.

Sjabloon achteraf toepassen (`mapsjablonen.pas_toe`): ontbrekende mappen worden aangelegd, een bestaande map met
hetzelfde pad (hoofdletters maken niet uit) wordt hergebruikt; er wordt niets verwijderd, verplaatst of hernoemd. De
regels voor de sjabloonmappen worden toegevoegd of bijgewerkt naar het sjabloon, regels op andere mappen blijven
staan, en `mapsjabloon` wordt gezet (ook als er al een ander sjabloon stond). Twee keer toepassen geeft hetzelfde
resultaat. Bestanden die niet aan een nieuwe extensieregel voldoen, blijven staan en worden geteld in
`afwijkende_bestanden`; `mappen_aangemaakt` en `mappen_bestaand` tellen ook tussenliggende mappen, `regels` is het
aantal regels uit het sjabloon. Het toepassen komt in de wijzigingenlijst (soort `sjabloon_toegepast`,
`doel_doctype` `Ruimte`, `naam` het sjabloon); een gearchiveerde ruimte geeft 403 met `ruimte_gearchiveerd`.

Op een plek geldt de regel van de map zelf of anders die van de dichtstbijzijnde bovenliggende map met een regel,
langs elke weg (browser, desktop-app, WebDAV, uploadlink, formulier):

- `alleen_lezen`: niets toevoegen, wijzigen, verplaatsen, hernoemen, verwijderen of vergrendelen, voor iedereen
  behalve een beheerder van de ruimte (en Administrator/System Manager): 403 met `map_alleen_lezen`. Ook een map
  waar ergens een alleen-lezenmap in zit, gaat niet naar de prullenbak. Een bestaande map opvragen met `maak_map`
  gaat wel.
- `extensies`: alleen bestanden met die extensies (hoofdletters maken niet uit), voor iedereen, ook bij hernoemen en
  verplaatsen naar de map: 417 met `extensie_niet_toegestaan`; de melding noemt de map en de toegestane soorten.

Bij installeren en migreren maakt een seed de sjablonen die er nog niet zijn; een bestaand (ook aangepast) sjabloon
blijft zoals het is, een verwijderd standaardsjabloon komt bij de volgende migratie terug.

- "Bedrijf" (soort `bedrijf`, standaard): Algemeen, Contracten, Projecten, Huisstijl (alleen lezen).
- "Project (BIM)" (soort `project`, standaard): 00 Projectinformatie, 01 Contract en financieel, 02 Correspondentie,
  03 Vergunningen, 04 Ontwerp (Architectuur, Constructie, Installaties), 05 BIM-modellen (Werk; Gedeeld: ifc, ifczip,
  ifcxml, bcf, bcfzip; Gepubliceerd: alleen lezen, ifc, ifczip, ifcxml, pdf), 06 Tekeningen (pdf, dwg, dxf),
  07 Berekeningen, 08 Uitvoering, 09 Oplevering en beheer, 10 Foto's (jpg, jpeg, png, heic, webp).

### API voor het beheer

Met de inlog van de gebruiker; methodes die iets veranderen alleen met POST. Antwoorden in `message`.

| Methode | Wie | Parameters | Antwoord |
|---|---|---|---|
| `openaec_storage.ruimtebeheer.archiveer` (POST) | beheerder van de ruimte of de omgeving | `ruimte`, `namens?` | `{ruimte, gearchiveerd: true, gearchiveerd_op, gearchiveerd_door, aantal_actieve_deellinks}`; nogmaals aanroepen verandert niets |
| `openaec_storage.ruimtebeheer.herstel` (POST) | idem | `ruimte`, `namens?` | `{ruimte, gearchiveerd: false, gearchiveerd_op: null, gearchiveerd_door: null, aantal_actieve_deellinks}` |
| `openaec_storage.ruimtebeheer.zet_quotum` (POST) | beheerder van de omgeving | `ruimte`, `quotum_gb` (0 of leeg = geen eigen limiet), `namens?` | `{ruimte, quotum_gb, limiet_bytes \| null}` |
| `openaec_storage.ruimtebeheer.lijst` | beheerder van de omgeving: alle ruimtes; anders de ruimtes waarvan je beheerder bent | `gearchiveerd?` (leeg = alle, 0 = actief, 1 = gearchiveerd) | `[{name, naam, soort, gearchiveerd, gearchiveerd_op, gearchiveerd_door, quotum_gb, mapsjabloon, aantal_leden, aantal_actieve_deellinks, gebruikt_bytes, limiet_bytes \| null}]`, op naam |
| `openaec_storage.ruimtebeheer.activiteit` | beheerder van de ruimte of de omgeving | `ruimte`, `vanaf?` (datum of datum-tijd), `start?` (0), `aantal?` (50, hoogstens 500) | `{ruimte, totaal, start, aantal, meer, regels: [{volgnummer, tijd, soort, doel_doctype, doel, naam, door, door_naam, namens, namens_naam}]}`, nieuwste eerst |
| `openaec_storage.mapsjablonen.pas_toe` (POST) | beheerder van de ruimte of de omgeving | `ruimte`, `mapsjabloon`, `namens?` | `{ruimte, mapsjabloon, mappen_aangemaakt, mappen_bestaand, regels, afwijkende_bestanden}` (zie Sjabloon achteraf toepassen) |
| `openaec_storage.api.quotum_ruimte` | leden van de ruimte, beheerder van de omgeving | `ruimte` | `{gebruikt_bytes, limiet_bytes \| null}` |
| `openaec_storage.mapsjablonen.sjablonen` | ingelogd | `soort?` (`team` geeft ook de sjablonen van soort `bedrijf`) | `[{naam, omschrijving, soort, standaard, mappen: [{map: null, pad, weergavenaam, volgorde, pictogram, extensies: [..], alleen_lezen}]}]` |
| `openaec_storage.mapsjablonen.regels` | leden van de ruimte | `ruimte` | `[{map, pad, weergavenaam, volgorde, pictogram, extensies: [..], alleen_lezen}]` |

`namens` (user-id) is alleen voor het auditspoor van een serviceaanroep uit het beheer. Het telt alleen als de
aanroeper Administrator of System Manager is en `namens` een bestaande, actieve gebruiker is; anders wordt het
genegeerd (een onbekende gebruiker met een waarschuwing in het log), zonder weigering. De rechten blijven altijd die
van de echte aanroeper. In de wijzigingenlijst staan beide: de aanroeper in `owner` (`door` in `activiteit`) en de
gebruiker in het veld `namens`. `gearchiveerd_door` wordt bij een geldige `namens` die gebruiker. Ook een gewijzigd
quotum komt in de wijzigingenlijst (soort `quotum_gewijzigd`, `naam` bijv. `5 GB`).

Een ruimte met sjabloon aanmaken gaat zoals elke ruimte (beheerder van de omgeving), met het veld `mapsjabloon`,
bijv. `frappe.client.insert` met `{"doctype": "Ruimte", "naam": "…", "soort": "project", "mapsjabloon":
"Project (BIM)", "leden": […]}`.

### Foutcodes

Bij een weigering met een vaste reden staat naast de melding een `foutcode` in het JSON-antwoord (op hetzelfde niveau
als `exc_type` en `_server_messages`), zodat de browser, de desktop-app en de gateway de reden kennen zonder de tekst
te lezen.

| Foutcode | HTTP | Wanneer |
|---|---|---|
| `ruimte_gearchiveerd` | 403 | Schrijven in een gearchiveerde ruimte, of een deellink naar een gearchiveerde ruimte |
| `quotum_pool_vol` | 417 | Het quotum van de omgeving is vol |
| `quotum_ruimte_vol` | 417 | Het eigen quotum van de ruimte is vol |
| `map_alleen_lezen` | 403 | Wijzigen in een alleen-lezenmap zonder beheerder van de ruimte te zijn |
| `extensie_niet_toegestaan` | 417 | Bestandssoort niet toegestaan in deze map |
| `ongeldige_naam` | 417 | Naam die niet kan (zie Geldige namen): nieuw bestand, nieuwe map, hernoemen of verplaatsen |
| `gebruiker_onbekend` | 417 | `deel` met `soort: "gebruiker"` voor een gebruiker die niet bestaat (melding "Kies een bestaande gebruiker om mee te delen") |
| `te_groot_voor_batch` | 417 | Eén bestand in `upload_klein`/`download_klein` is groter dan 1 MiB (gebruik de gewone weg) |
| `batch_te_groot` | 417 | Te veel in één verzoek (meer dan 64 bestanden, 16 MiB of 1000 mappen); bij `download_klein` per bestand voor wat er niet meer bij past |
| `alleen_lezen_deling` | 403 | Wijzigen (uploaden, map maken, verplaatsen, hernoemen, verwijderen, vergrendelen) in iets dat met je gedeeld is om te bekijken |
| `buiten_deling` | 403 | Wie via een deling bij de ruimte kan, vraagt iets buiten de gedeelde map of het gedeelde bestand (ook: de gedeelde map zelf hernoemen, verplaatsen of verwijderen) |
| `deling_ingetrokken` | 403 | De deling op deze plek is ingetrokken (ook midden in een upload of sync) |
| `deling_verlopen` | 403 | De deling op deze plek is verlopen |
| `deling_onbekend` | 403 | `deling` bestaat niet, is een link of is met iemand anders gedeeld |
| `deling_weg` | 404 | De gedeelde map is verwijderd (bij `deling` als wortel) |
| `deling_geen_map` | 417 | `deling` is een gedeeld bestand; dat kan niet als bovenste map |

In een batch (`upload_klein`, `download_klein`, `maak_mappen`) staat de foutcode per bestand of pad in het antwoord,
niet in `foutcode` van het hele antwoord.

## Geldige namen

Een naam die op Windows niet kan, komt er niet in: daar is zo'n bestand met normale paden niet te openen, te
verplaatsen of te verwijderen, en een sync-programma blijft er dan op vastlopen. Ongeldig zijn (`namen.py`, dezelfde
regels als de desktop-app):

- de apparaatnamen `CON`, `PRN`, `AUX`, `NUL`, `CONIN$`, `CONOUT$`, `COM0`–`COM9` en `LPT0`–`LPT9` (ook met de
  superscriptcijfers ¹²³), zonder onderscheid tussen hoofd- en kleine letters en ook met een extensie (`nul.txt`,
  `con.log`);
- namen die op een punt of spatie eindigen;
- de tekens `<>:"/\|?*` en controletekens;
- namen langer dan 255 tekens, en een lege naam, `.` of `..`.

Een nieuwe upload, een nieuwe map en een nieuwe naam bij hernoemen of verplaatsen met zo'n naam geven 417 met de
foutcode `ongeldige_naam` en de reden in de melding, langs elke weg: browser, desktop-app (ook de batch), WebDAV (de
gateway geeft dan 403 Forbidden), uploadlinks en overdrachten. Bestaande bestanden en mappen met zo'n naam blijven
staan: een bestand kan nog een nieuwe versie krijgen onder dezelfde naam, en kan alleen hernoemd worden naar een
geldige naam; een bestaande map blijft op te vragen met `maak_map`.

## Delen vanuit de desktop-app

Voor de deel-dialoog (een bestand of map delen met een collega of een link, en zien wie er toegang heeft). Alle
methodes onder `/api/method/openaec_storage.api.<methode>`, met de inlog van de gebruiker; antwoorden in `message`.

| Methode | Wie | Parameters | Antwoord |
|---|---|---|---|
| `collegas` | wie in `ruimte` mag delen (schrijver of beheerder; een gearchiveerde ruimte geeft 403 met `ruimte_gearchiveerd`), of een beheerder van de omgeving (ook zonder `ruimte`); anders 403 | `tekst?` (zoekt in e-mailadres en volledige naam), `ruimte?` | `[{gebruiker, naam, lid_als}]`, hoogstens 20, op naam: actieve systeemgebruikers (geen websitegebruikers, gasten of Administrator); `lid_als` is de rol in `ruimte` (`beheerder`, `schrijver`, `lezer`, `gast`) of `null` |
| `deel` (POST) | schrijver of beheerder op die plek | `doel_doctype` (`Opslagmap`/`Opslagbestand`), `doel`, `soort` (`link` of `gebruiker`), `gebruiker?`, `rechten` (`lezen`; `schrijven` alleen met een gebruiker; `uploaden` alleen met een link naar een map), `wachtwoord?`, `verloopt?` | `{deling, token, rechten, url}`; een onbekende gebruiker geeft 417 met `gebruiker_onbekend`. Met een gebruiker krijgt die een melding en een e-mail (zie Gedeeld met mij) |
| `delingen` | wie de ruimte kan lezen | `doel_doctype`, `doel` | `[{name, soort, gebruiker, rechten, verloopt, token, creation, beveiligd, verlopen, gemaakt_door, gemaakt_door_naam}]`: de niet ingetrokken delingen, ook verlopen. `beveiligd` zegt of er een wachtwoord op zit (de hash komt nooit mee); `verlopen` is `true` als `verloopt` voorbij is |
| `trek_in` (POST) | schrijver of beheerder van de ruimte | `deling` | — |

## Gedeeld met mij

Een map of bestand dat iemand met een collega deelt (`deel` met `soort: "gebruiker"`), is voor die collega bruikbaar
zonder lid van de ruimte te zijn: zien, openen, bewerken (bij `schrijven`) en syncen. De rechten komen uit de deling
(`lezen` of `schrijven`) en gelden voor de map en alles eronder; de ruimte zelf blijft voor hem verborgen. Leden van de
ruimte houden hun rol; een deling kan hen op een plek meer rechten geven.

Alle methodes onder `/api/method/openaec_storage.api.<methode>`, met de inlog van de ontvanger; antwoorden in
`message`.

| Methode | Parameters | Antwoord |
|---|---|---|
| `gedeeld_met_mij` | — | `[{deling, doel_doctype, doel, naam, ruimte, ruimte_naam, rechten, gedeeld_door, gedeeld_door_naam, sinds, verloopt, al_lid, rol}]`, nieuwste eerst. Een gedeeld bestand heeft ook `map`, `grootte` en `versie` |
| `bladeren` | `deling`, `map?` (leeg = de gedeelde map) | `{mappen, bestanden}` zoals altijd |
| `boom` | `deling`, `start?`, `aantal?` | als hierboven, met `wortel` (de gedeelde map; zelf niet in `mappen`, de mappen direct eronder hebben hem als `bovenliggende`) |
| `wijzigingen` | `deling`, `sinds?`, `limiet?` | `{cursor, meer, wijzigingen}`: alleen de wijzigingen over de gedeelde map, een map eronder (ook een verwijderde) of een bestand daarin; de cursor loopt over de hele ruimte |
| `maak_map` (POST) | `deling`, `naam`, `bovenliggende?` (leeg = de gedeelde map) | `{map, nieuw}` |
| `maak_mappen` (POST) | `deling`, `paden` (binnen de gedeelde map) | `{mappen, fouten}` |
| `upload_klein` (POST) | `deling`, `bestanden` (`pad` binnen de gedeelde map, of `map` daarbinnen) | `{resultaten}` |

`ruimte` mag bij deze methodes weg (hij volgt uit de deling); staat hij erbij, dan moet hij kloppen. Zonder `deling`
werkt alles zoals vroeger. De methodes per bestand of map (`upload_start`/`upload_klaar` met `map` = de gedeelde map
of een map daarin, `download`, `download_klein`, `blokken`, `versies`, `info`, `verplaats`, `verplaats_map`,
`verwijder`, `verwijder_map`, `vergrendel`) hebben geen `deling` nodig: ze volgen de rechten op de plek, en die
komen ook uit de deling. `deling` bij de methodes hierboven moet een deling met de ingelogde gebruiker zijn die nu
geldt (anders `deling_onbekend`, `deling_ingetrokken`, `deling_verlopen` of `deling_weg`), en een map
(`deling_geen_map`).

Gedrag:

- **Binnen de wortel.** Wie geen lid is, komt nooit buiten de gedeelde map: een map, pad of bestand ernaast of erboven
  geeft 403 met `buiten_deling` (ook in de batch, per bestand of pad). Een pad met `..` is gewoon een ongeldige naam
  (`ongeldige_naam`). De gedeelde map zelf hernoemen, verplaatsen of verwijderen gaat niet (`buiten_deling`):
  daarvoor is schrijfrecht op de map erboven nodig. Binnen de map kan alles wat de rechten toestaan.
- **Lezen tegenover schrijven.** Bij `lezen` geven uploaden, mappen maken, verplaatsen, hernoemen, verwijderen en
  vergrendelen 403 met `alleen_lezen_deling`; bladeren, downloaden, versies en de wijzigingenlijst werken.
- **Quotum en mapregels** gelden zoals voor een lid: `quotum_pool_vol`, `quotum_ruimte_vol`, `map_alleen_lezen` (de
  ontvanger is geen beheerder van de ruimte) en `extensie_niet_toegestaan`. Een gearchiveerde ruimte is alleen te
  lezen (`ruimte_gearchiveerd`) en staat niet in `gedeeld_met_mij`.
- **Een gedeeld bestand** synct niet als map (`deling_geen_map`): het is te openen en te downloaden, en bij
  `schrijven` kan er een nieuwe versie komen (`upload_start` met `map` en `naam` van het bestand en `basis_versie`).
  Een bestand ernaast uploaden, het bestand hernoemen of verwijderen gaat niet (`buiten_deling`).
- **Intrekken en verlopen.** Na `trek_in` (of als `verloopt` voorbij is) heeft de ontvanger meteen geen toegang meer:
  elke volgende aanroep op die plek geeft 403 met `deling_ingetrokken` of `deling_verlopen`, ook `upload_klaar` van
  een upload die al liep en de batch (per bestand). Een andere deling die nog geldt (bijv. op een submap) blijft
  werken. Bij intrekken krijgt de ontvanger geen mail.
- **Melding.** Bij `deel` met een gebruiker krijgt de ontvanger een melding in de desk (Notification Log, soort
  `Alert`, met `link` naar `/opslag?deling=<deling>`) en een e-mail met de naam, wie het deelde, wat hij ermee kan en
  dezelfde link. De mail gaat via de mail van de site (in de wachtrij). Lukt de melding of de mail niet, bijvoorbeeld
  omdat er geen uitgaand e-mailaccount is, dan blijft de deling gewoon staan en staat het in het foutlog ("Melding
  over een deling niet gemaakt" of "Mail over een deling niet verstuurd").
- **`al_lid`.** Wie ook lid is van de ruimte, ziet de deling in `gedeeld_met_mij` met `al_lid: true` en zijn `rol`:
  de deling kan op die plek meer rechten geven dan de rol. Twee delingen op dezelfde map of hetzelfde bestand geven
  één regel met het hoogste recht.

Op `/opslag` staat het tabblad **Gedeeld met mij**: per map of bestand wie het deelde, wat je kunt en tot wanneer.
Een map open je zoals een ruimte (bladeren, uploaden, mappen maken, hernoemen en verwijderen binnen de rechten); een
bestand download je, en bij bewerken kun je een nieuwe versie uploaden. De link uit de mail en de melding
(`/opslag?deling=<deling>`) opent de gedeelde map meteen.

## Desktop-sync: veel kleine bestanden

Voor mappen met duizenden kleine bestanden zijn er methodes die veel in één verzoek doen. Ze volgen per bestand
dezelfde regels als de gewone weg (`upload_start`/`upload_klaar`, `download`, `maak_map`): rechten, quotum van pool en
ruimte, mapregels (extensies, alleen lezen), gearchiveerde ruimtes, versies en conflicten, geldige namen en de
wijzigingenlijst. Een fout bij één bestand draait alleen dat bestand terug en houdt de andere niet op. Alle methodes
onder `/api/method/openaec_storage.api.<methode>`, met de inlog van de gebruiker; antwoorden in `message`.

| Methode | Parameters | Antwoord |
|---|---|---|
| `mogelijkheden` | — | `{upload_klein: {max_bestand, max_aantal, max_bytes}, download_klein: {…}, boom: true, maak_mappen: true, deling: true}`. Een oudere server kent deze methode niet; de app gebruikt dan de gewone weg. `deling`: de parameter `deling` (zie Gedeeld met mij) bestaat |
| `boom` | `ruimte`, `start?` (0), `aantal?` (10 000, hoogstens 50 000), `deling?` | `{mappen: [{name, naam, bovenliggende, modified}], bestanden: [{name, naam, map, grootte, huidige_versie, modified}], meer}`: alle mappen en een pagina bestanden (op `name`), zonder de prullenbak. Zonder `deling` alleen voor leden van de ruimte; met `deling` alles onder de gedeelde map, plus `wortel` |
| `maak_mappen` (POST) | `ruimte`, `paden` (lijst mappaden met `/`, hoogstens 1000), `deling?` (dan zijn de paden binnen de gedeelde map) | `{mappen: {pad: map}, fouten: {pad: {fout, foutcode}}}`; ontbrekende mappen (ook tussenliggende) worden aangemaakt zoals bij `maak_map`, bestaande hergebruikt (hoofdletters maken niet uit) |
| `upload_klein` (POST, multipart/form-data) | `ruimte`, `bron?` (`sync`), `deling?` (dan zijn `pad` en `map` binnen de gedeelde map), `bestanden` (JSON-lijst: per bestand `naam`, `map` (id) of `pad` (mappad; ontbrekende mappen worden aangemaakt), `grootte`, `blokhash` (BLAKE3 van de inhoud), `basis_versie?`, `sha256?`, `veld`), en de inhoud: alles achter elkaar in één bestandsveld `inhoud` (gesneden op `grootte`; zo doet de desktop-app het) of per bestand een bestandsveld met de naam uit `veld` (standaard `f<index>`). Met `sha256` wordt de ontvangen inhoud gecontroleerd | `{resultaten: [{index, ok: true, bestand, naam, versie, conflict, map} \| {index, ok: false, fout, foutcode}]}`, in de volgorde van `bestanden` |
| `download_klein` | `bestanden` (lijst bestand-id's of `{bestand, versie}`) | `{bestanden: [{index, ok: true, bestand, naam, versie, grootte, blokhash, inhoud (base64)} \| {index, ok: false, fout, foutcode}]}`; de inhoud komt tegelijk uit S3 |

Grenzen: hoogstens 1 MiB per bestand, en 64 bestanden of 16 MiB per verzoek (`KLEIN_MAX_*` in `api.py`). Een groter
bestand gaat langs de gewone weg in delen van 8 MiB. Bij `upload_klein` schrijft de server de inhoud zelf naar S3
(`s3.zet`); bij een conflict (een verouderde `basis_versie`, of een bestand dat iemand anders vergrendelde) komt er
een conflictkopie naast het origineel, net als bij `upload_klaar`.


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

### Meetbench

`scripts/meetbench/meet.sh <meting oud> <meting nieuw> [werkmap]` zet een lokale bench op met Docker
(`frappe/erpnext:v16.51.1`, mariadb 11.8, valkey, moto als S3 en gunicorn met 4 werkers van 4 draden, met deze
werkkopie van de app), meet twee builds van het meetprogramma van de desktop-sync (`cargo build --release --example
meting` in `crates/openaec-sync`) en ruimt containers en volumes daarna op (`MEETBENCH_LATEN=1` laat ze staan).
Gemeten wordt één sync-ronde: upload naar een lege ruimte en download naar een lege map, voor 2000 bestanden van
1–20 KB en voor 5 bestanden van 50 MB.

Meetresultaten (Windows 11, Docker Desktop, gunicorn met 4 werkers van 4 draden, moto als S3; één ronde, standaard 8
overdrachten tegelijk). "Oud" is versie 0.5.0 (elk bestand na elkaar), "nieuw" deze versie:

| Set | Richting | Oud | Nieuw | Sneller |
|---|---|---|---|---|
| 2000 bestanden van 1–20 KB in 40 mappen | upload | 144,8 s | 19,6 s | 7,4× |
| 2000 bestanden van 1–20 KB in 40 mappen | download | 97,0 s | 6,9 s | 14× |
| 5 bestanden van 50 MB | upload | 5,4 s | 3,5 s | 1,5× |
| 5 bestanden van 50 MB | download | 4,2 s | 2,0 s | 2,1× |

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
- Een gearchiveerde ruimte staat niet in de wortel (net als in `api.ruimtes`). Weigeringen volgen de API: 403
  (gearchiveerd, alleen-lezenmap) en 417 (extensie) worden 403 Forbidden, een vol quotum 507 Insufficient Storage.
  Let op: programma's die via een tijdelijk bestand opslaan (bijv. `.tmp`), kunnen dat niet in een map die alleen
  bepaalde extensies toestaat.
- Instellingen: `OPENAEC_BASIS` (adres van de klantomgeving) en `OPENAEC_DAV_ADRES` (standaard `0.0.0.0:8310`).

```bash
cd gateway && cargo test
bash gateway/build-context.sh /tmp/dav-ctx && docker build -f /tmp/dav-ctx/Containerfile -t openaec-dav /tmp/dav-ctx
```
