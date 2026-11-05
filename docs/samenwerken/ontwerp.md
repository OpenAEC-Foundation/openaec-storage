# Samenwerken aan bestanden in de SuperCloud: ontwerp van de onderlaag

Stand: 5 november 2026. Status: voorstel, nog niet beoordeeld door Ethan of door de owner van de SuperCloud-kern.

## 0. Samenvatting

De SuperCloud bewaart projectbestanden met versies, rechten, delingen en een slot per bestand. Twee mensen kunnen
nu niet goed tegelijk aan één bestand werken. Wie als tweede opslaat, krijgt een conflictkopie, en iemand moet de
twee versies met de hand samenvoegen.

Dit ontwerp beschrijft de onderlaag voor samenwerken: het datamodel, de diensten en de API. Er komen geen schermen
in. De kern is één idee: **een bouwbestand is een verzameling objecten met een vaste sleutel**. In een IFC-model en
in een planning van Open Planner Studio (OPS) is dat de GlobalId. Met die sleutel kunnen we:

1. twee versies per object vergelijken ("wat is er veranderd"),
2. gelijktijdige wijzigingen per object samenvoegen, ook na offline werken,
3. per object uitchecken in plaats van per bestand,
4. opmerkingen aan een object hangen die ook in de volgende versie blijven kloppen.

Microsoft 365 kan dit alleen voor Word, Excel en PowerPoint. Voor IFC, tekeningen en planningen geeft het een slot
of een conflictkopie. Daar kan de SuperCloud beter zijn.

De eerste bouwstappen staan los van de kern van de SuperCloud: een Rust-bibliotheek die IFC-versies vergelijkt en
samenvoegt, en de koppeling in de desktop-sync. De diensten op de server (aanwezigheid, opmerkingen, volgen en
meldingen, uitchecken per object) beschrijf ik hier, maar bouw ik pas als de kern gelezen is. Zie §9.

Hoe dit meegroeit naar modellen van meerdere gigabytes en honderden deelnemers, en hoe het tientallen jaren meegaat,
staat in §12. Daar staan ook de grenzen tussen de lagen, met wat elke laag van de andere mag weten.

## 1. Opdracht en grenzen

- Opdracht van Ethan (5 november 2026): "Ga in de Supercloud bestands collaboratie inbouwen. Beter dan ms 365.
  Volledig autonoom." Aanvulling dezelfde dag: "Je bouwt voorlopig enkel de onderliggende architectuur". Dus: geen
  schermen, geen tekst voor gebruikers, geen UI-demo. Wel bewijs met tests en een proef met twee clients.
- De repo van de SuperCloud-kern (`openaec-supercloud`) is voor Nozzit niet zichtbaar. Ik heb hem niet gelezen.
  Alles wat dit ontwerp over de kern zegt, is een aanname. §9 noemt ze.
- Ik werk alleen in de eigen repo's: openaec-storage, openaec-cloud-desktop en open-bim-platform. Waar de code
  uiteindelijk komt, beslist Ethan.

Woorden in dit document:

| Woord | Betekenis |
|---|---|
| object | Een IFC-instantie met een GlobalId (een subtype van IfcRoot): een wand, een verdieping, een taak, een relatie, een eigenschappenset |
| knoop | Een IFC-instantie zonder GlobalId: geometrie, plaatsing, een eigenschapswaarde, eenheden |
| basis | De versie waarop iemand begon te werken |
| ons, hun | Onze eigen wijziging, en de wijziging die intussen op de server kwam |
| samenvoegen | Uit basis, ons en hun één nieuwe versie maken (three-way merge) |
| conflict | Ons en hun wijzigden hetzelfde attribuut van hetzelfde object op een andere manier |
| uitchecken | Een bestand vergrendelen, zodat alleen jij een nieuwe versie maakt (bestaat al als `vergrendel`) |
| uitchecken per object | Een groep objecten in een bestand claimen, zodat een ander die objecten niet wijzigt |

## 2. Wat de SuperCloud nu kan

Bronnen: openaec-storage op `main` (commit `e87b870`, 5 november 2026) en openaec-cloud-desktop op `main`.

| Onderdeel | Hoe het nu werkt | Code |
|---|---|---|
| Ruimtes en rollen | Beheerder, schrijver, lezer, gast. Alle rechten volgen uit het lidmaatschap of uit een deling | `openaec_storage/rechten.py` |
| Bestanden met versies | `Opslagbestand` met `huidige_versie`; elke versie is een `Bestandsversie` met `volgnummer`, S3-sleutel, grootte, sha256, bron en maker | `api.py` `_registreer` (r. 361) |
| Conflictregel | Een upload wint alleen als `basis_versie` gelijk is aan de huidige versie. Anders komt er een conflictkopie "naam (conflict Anna 2026-11-05 1405).ifc" naast het origineel | `api.py` r. 8-10, `_conflictnaam` (r. 99) |
| Delta per blok | Blokken van 8 MiB met een BLAKE3-hash. Ongewijzigde blokken kopieert de server zelf uit de vorige versie | `api.py` `_herbruikbare_blokken` (r. 308) |
| Versies opvragen | `versies(bestand)` en `download(bestand, versie)` | `api.py` r. 217, 417 |
| Uitchecken (slot per bestand) | `vergrendel` geeft 8 uur een slot (`SLOT_UREN`); een upload van een ander wordt dan een conflictkopie; verwijderen en verplaatsen weigert met `bestand_vergrendeld`; een beheerder kan het slot openbreken | `api.py` r. 948-997 |
| Delen | Met een collega (`lezen`, `schrijven`, `beheren`) of als link, met wachtwoord en vervaldatum | `api.py` `deel` (r. 1001), `delen.py` |
| Wijzigingenlijst | `Opslagwijziging` met een oplopend volgnummer per site; `wijzigingen(ruimte, sinds)` geeft alles na een cursor | `api.py` `_log` (r. 110), `wijzigingen` (r. 847) |
| Meldingen | Alleen bij delen: een Notification Log en een e-mail aan de ontvanger | `api.py` `_meld_ontvanger` (r. 1103) |
| Projectdossier | Revisie en status per versie (concept, ter beoordeling, vrijgegeven, vervallen) | `dossier.py` |
| Desktop-sync | Rust-crate `openaec-sync`, één opslag-trait (`Opslag`, `motor.rs` r. 75) met één implementatie voor deze API. Lokale stand in `.openaec/staat.json` (versie per bestand, geen inhoud). Vraagt de wijzigingenlijst elke 30 s op | openaec-cloud-desktop: `crates/openaec-sync/src/motor.rs`, `src-tauri/src/bewaking.rs` r. 19-21 |
| WebDAV | Rust-gateway; schrijven wordt een nieuwe versie met de huidige versie als basis | `gateway/` |

Wat ontbreekt voor samenwerken:

- **Geen push.** `frappe.publish_realtime` komt niet voor in de code. Een client hoort alleen iets door te vragen.
  De desktop-app vraagt elke 30 seconden.
- **Geen aanwezigheid.** Niemand ziet wie een bestand open heeft, behalve via een slot.
- **Geen opmerkingen** op een bestand of op een plek in een bestand.
- **Geen volgen** en geen meldingen bij een nieuwe versie (issue #3).
- **Geen haak bij een nieuwe versie** voor andere apps op de server (issue #7).
- **Geen samenvoegen.** Bij gelijktijdig werken komt er altijd een conflictkopie.
- **Geen verschil tussen versies.** Je kunt twee versies downloaden, maar niets zegt wat er veranderde.
- **Geen terugzetten van een versie** in één stap. Nu: de oude versie downloaden en opnieuw uploaden.
- **Uitchecken alleen per bestand.** In een groot model blokkeert één persoon iedereen.

Gevonden risico (vermoeden, nog te meten): `_registreer` vergelijkt de basis met de huidige versie zonder eerst de
regel van het bestand te vergrendelen. De uploadlink (`delen.py` r. 260) vergrendelt wel, met `select … for update`.
Frappe beschermt deels: `Document.save()` roept `check_if_latest()` aan, die de regel met `for update` leest en de
wijzigingstijd vergelijkt (ook met `ignore_permissions`). Wat er bij twee uploads met dezelfde basis gebeurt, hangt
daardoor af van het moment van lezen en van het isolatieniveau van de database:

- Onder REPEATABLE READ (de standaard van MariaDB) wordt de tweede een conflictkopie, of hij krijgt een
  `TimestampMismatchError`. Stil overschrijven volgt daar niet uit. Wel krijgt de client dan een fout in plaats van
  de beloofde conflictkopie.
- Onder READ COMMITTED kan de eerste controle verouderd zijn voordat `get_doc()` de nieuwere versie leest. Dan kan
  de tweede upload een gewone nieuwe versie worden, op een verouderde basis.
- Twee uploads van een *nieuw* bestand met dezelfde naam in dezelfde map: beide zien geen bestaand bestand en voegen
  allebei een `Opslagbestand` toe. `check_if_latest` helpt daar niet, want het is een insert.

Stap 4 (§7) toetst deze volgordes met een test die twee databaseverbindingen echt laat wisselen, en meet welk
isolatieniveau Frappe gebruikt. Voor `bij_conflict="weiger"` is een atomaire vergelijking hoe dan ook nodig. Stap 4
legt een nieuwe versie daarom vast onder een slot op het bestand (of op de map, bij een nieuw bestand). De proef met
twee clients (stap 5) meet het daarna nog eens langs de echte HTTP-weg.

Een tweede gevonden risico (bevestigd door Sol in ronde 10): een versienummer kan terugkomen. `verplaats(…,
overschrijf=1)` (`api.py` r. 509-523) verplaatst de laatste `Bestandsversie` van de bron naar het doel en zet de bron
terug op het vorige volgnummer, in de prullenbak. `herstel()` (r. 443) maakt de bron weer actief, en `_registreer`
telt vanaf dat lagere nummer verder. Dan wijst "F1 versie 2" eerst naar inhoud A en later naar inhoud B. En
`verplaats` leest het doel zonder slot (r. 487): twee verplaatsingen tegelijk naar hetzelfde doel geven twee regels
hetzelfde nummer, en een regel kan zo van het ene doel naar het andere schuiven (Sol, rondes 12 en 13). De
WebDAV-gateway gebruikt die route bij elk opslaan via een tijdelijk bestand (`gateway/src/fs.rs` r. 305). Stap 4 lost
dit op door de basis niet meer op het nummer te laten steunen (§5.5).

## 3. Wat Microsoft 365 doet

"Gelezen" betekent: ik las de officiële bron. "Eigen kennis" betekent: niet met een bron nagegaan op 5 november 2026.

| Functie | Microsoft 365 | Zekerheid |
|---|---|---|
| Tegelijk bewerken | Word, Excel en PowerPoint, voor bestanden in OneDrive of SharePoint. Alleen .docx, .xlsx en .pptx. Alle bewerkers hebben bewerkrecht nodig; niet met IRM-beveiliging. Wijzigingen verschijnen binnen seconden als AutoSave aan staat | Gelezen: [support.microsoft.com, Document collaboration and co-authoring](https://support.microsoft.com/en-gb/sharepoint/get-started-with-sharepoint/document-collaboration-and-co-authoring). Snelheid: eigen kennis |
| Andere bestandssoorten | Geen tegelijk bewerken. Een DWG, IFC of PDF krijgt in OneDrive bij gelijktijdig wijzigen een conflictkopie, of de laatste opslag wint | Afgeleid uit de bron hierboven; gedrag eigen kennis |
| Aanwezigheid | Je ziet wie het document open heeft, en in Word en Excel waar hun cursor staat | Eigen kennis |
| Opmerkingen met @noemen | Iemand noemen stuurt een e-mail met de opmerking. Heeft die persoon geen toegang, dan vraagt Word of je het bestand met hem deelt | Fragment: [office365itpros.com](https://office365itpros.com/2020/05/02/mentions-word-comments/) |
| Versies en terugzetten | Standaard 500 hoofdversies per bestand in een nieuwe bibliotheek (instelbaar van 100 tot 50 000). Elke versie is terug te zetten | Gelezen: [learn.microsoft.com, default version limits](https://learn.microsoft.com/en-us/SharePoint/set-default-org-version-limits). Terugzetten: eigen kennis |
| Delen met rechten | Links voor iedereen, voor de organisatie of voor bepaalde personen; bekijken of bewerken; vervaldatum en wachtwoord | Eigen kennis |
| Uitchecken | SharePoint kan uitchecken verplicht maken. Een uitgecheckt bestand is voor anderen alleen te lezen tot het inchecken. Tegelijk bewerken en verplicht uitchecken gaan niet samen | Fragment: [mrsharepoint.com](https://www.mrsharepoint.com/do-you-have-to-check-out-a-document-in-sharepoint-online/) |
| Activiteit | Per bestand een activiteitenlijst (wie opende, bewerkte, deelde); in Word "wijzigingen sinds je laatste bezoek" | Eigen kennis |
| Meldingen | E-mail en een melding in de app bij @noemen en bij reacties op je opmerkingen | Eigen kennis |
| Offline | OneDrive synct offline wijzigingen. Office-bestanden voegt Office samen; andere bestanden krijgen een kopie met de naam van de computer | Eigen kennis |

Uit de AEC-wereld (alleen het samenwerkmodel):

- **Revit worksharing.** Een element wordt geleend zodra je het wijzigt; bij "Synchronize with Central" geef je het
  terug. Dit is uitchecken per object, maar alleen binnen Revit. Gelezen:
  [help.autodesk.com (Revit 2016)](https://help.autodesk.com/cloudhelp/2016/ENU/Revit-Collaborate/files/GUID-27E3B22D-79D3-44F5-A630-6E5F614585B4.htm).
- **Autodesk Construction Cloud, Design Collaboration.** Teams publiceren pakketten en vergelijken modelversies.
  Fragment: [help.autodesk.com](https://help.autodesk.com/cloudhelp/ENU/BIM360D-Design-Collaboration/files/About-Changes-in-Design/About-Change-Workflows/GUID-D4DF5D59-53EE-4C3A-A8AB-ADF85686BAD8.html).
- **Speckle.** Versies bestaan uit objecten; een vergelijking gaat per object-id. Fragment:
  [speckle.community](https://speckle.community/t/version-diffing-with-python-part-1/5614).
- **BCF 3.0** (buildingSMART). Topics, opmerkingen en standpunten (viewpoints) voor modelbesprekingen, als
  bestand of als REST-API. Gelezen: [github.com/buildingSMART/BCF-API](https://github.com/buildingSMART/BCF-API).
- **ifcmerge** (Bruno Postle, gebruikt door IfcGit in Bonsai). Voegt IFC-bestanden samen op de STEP-nummers
  (`#123`). Eigen kennis. Dat werkt alleen als het programma de nummers bij opslaan laat staan.

## 4. Waar wij beter kunnen zijn

1. **Samenvoegen van IFC, voor modellen en planningen.** M365 voegt alleen Office-bestanden samen. Wij voegen
   per object samen. Voorbeeld: Anna verschuift taak *Ruwbouw* in `Aannemer.ifc`, Piet wijzigt tegelijk de duur van
   *Afbouw*. Allebei slaan ze op vanaf versie 12. Versie 13 is Anna's wijziging, versie 14 bevat beide. Niemand ziet
   een conflictkopie.
2. **Uitchecken per object.** Revit kan dit, maar alleen in Revit. Wij doen het op de GlobalId, dus voor elk
   programma dat IFC schrijft. Voorbeeld: de constructeur checkt de kolommen van verdieping 3 uit; de architect
   werkt in hetzelfde bestand aan de gevel.
3. **Verschil per object.** Niet "het bestand is veranderd", maar "3 wanden verplaatst, 1 deur verwijderd, de
   brandwerendheid van W-12 van 30 naar 60 minuten". Voor een planning: "Ruwbouw schuift 2 weken op".
4. **Opmerkingen die meegaan.** Een opmerking hangt aan een object (GlobalId) en een standpunt (BCF), niet aan een
   regel of pagina. In de volgende versie staat hij nog op dezelfde wand. Is de wand weg of gewijzigd, dan zegt de
   API dat.
5. **Werkt offline, los van één transport.** De samenvoeglogica zit in een bibliotheek die de desktop-sync gebruikt.
   Die vraagt van de opslag alleen twee dingen: versies lezen, en "schrijf alleen als de basis nog klopt". De opslag
   van de SuperCloud is openaec-storage. Omdat de bibliotheek niets anders van de opslag weet, kan ze later ook op de
   server, in de browser of achter een ander transport draaien.
6. **Open formaten.** Alles werkt op IFC (ISO 16739) en BCF. Geen eigen formaat.

## 5. Architectuur

### 5.1 Drie lagen

```
 programma's: OPS, BIM-platform, PDF Studio, desktop-app, WebDAV, browser (later: schermen)
          │                         │                              │
          ▼                         ▼                              ▼
 ┌──────────────────────┐  ┌───────────────────────────────┐  ┌──────────────────────────────┐
 │ 3. formaatkennis     │  │ 2. samenwerkdiensten           │  │ 1. opslag                    │
 │ (bibliotheek)        │  │ (server, per bestand)          │  │ (bytes en versies)           │
 │                      │  │                                │  │                              │
 │ - IFC lezen          │  │ - gebeurtenissen + push        │  │ openaec-storage:             │
 │ - objectmodel        │  │ - aanwezigheid                 │  │   Bestandsversie, S3,        │
 │ - verschil           │  │ - opmerkingen + @noemen        │  │   basis_versie, slot         │
 │ - samenvoegen        │  │ - volgen + meldingen           │  │   blokken van 8 MiB met      │
 │ - opslaan-lus        │  │ - uitchecken per object        │  │   BLAKE3, versies            │
 │   (lees, voeg samen, │  │ - activiteit per bestand       │  │                              │
 │    schrijf)          │  │                                │  │                              │
 └──────────────────────┘  └───────────────────────────────┘  └──────────────────────────────┘
   draait in de client        draait op de server              één trait in de client
   (later ook op de server)   (Frappe-app, plek: zie §9)        (motor.rs `Opslag`)
```

- **Laag 1, opslag**, heeft maar twee dingen nodig: versies lezen en "schrijf deze inhoud alleen als de huidige
  versie nog N is" (vergelijk-en-schrijf). openaec-storage heeft dat met `basis_versie` (en in het voorstel van stap
  4 met `basis_bestand` en `basis_versie_id`). Meer hoeft de opslag niet van samenvoegen te weten. De trait in de
  client houdt de bibliotheek los van de vorm van de API; dat helpt de levensduur. Een tweede opslag is geen doel.
- **Laag 2, samenwerkdiensten**, gaat over mensen: wie is er, wie zegt wat, wie wil wat weten, wie werkt aan welke
  objecten. Dit is kleine data met rechten per bestand. Het hoort naast de bestanden op de server.
- **Laag 3, formaatkennis**, weet hoe een IFC-bestand in objecten uiteenvalt. Dit is een losse bibliotheek zonder
  netwerk of database. De client gebruikt hem (desktop-sync nu, de apps later via WebAssembly). De server kan hem
  later als hulpprogramma draaien voor `verschil` en voor samenvoegen langs WebDAV en de browser.

### 5.2 Drie manieren om tegelijk te werken

| Manier | Wanneer | Hoe | Status |
|---|---|---|---|
| A. Voorkomen | Je weet vooraf wie waaraan werkt | Uitchecken per bestand (bestaat) of per object (nieuw, §5.7). Aanwezigheid laat zien wie het bestand open heeft | Ontwerp |
| B. Samenvoegen bij opslaan | Iedereen werkt in een eigen kopie: desktop-programma's, offline, OPS in de browser | Opslaan-lus met vergelijk-en-schrijf; bij een verouderde basis samenvoegen per object (§5.6) | Wordt gebouwd |
| C. Live samen bewerken | Twee mensen in dezelfde app, op hetzelfde moment | Een sessie per bestand met een door de server genummerde stroom van wijzigingen per object en attribuut; de laatste wijziging per attribuut wint (zoals Figma). Een client schrijft af en toe een gewone versie weg | Later (§5.11) |

B is de basis. A maakt B rustiger: minder echte conflicten. C bouwt op hetzelfde objectmodel als B: een wijziging
is "attribuut X van object G kreeg waarde W".

### 5.3 Datamodel

Bestaand en ongewijzigd: `Ruimte`, `Opslagmap`, `Opslagbestand`, `Bestandsversie`, `Opslagvergrendeling`,
`Opslagdeling`, `Opslagwijziging`.

Nieuw (namen zijn een voorstel):

| DocType | Velden | Waarvoor |
|---|---|---|
| `Bestandsversie` (uitbreiding) | `samengevoegd_uit` (JSON: `{basis, hun}` met de `versie_id` van die regels, nooit versienummers), `samenvoegrapport` (JSON, kort) | Zien dat versie 14 een samenvoeging is van 12 (basis), 13 (hun) en de upload van Piet |
| `Opslagopmerking` | `ruimte`, `bestand`, `versie` (waarop geschreven), `draad` (Link naar de eerste opmerking; leeg = nieuwe draad), `tekst`, `anker` (JSON, zie §5.9), `status` (`open`, `opgelost`; alleen op de eerste), `opgelost_door`, `opgelost_op`, `bewerkt_op`, `verwijderd_op`, `bcf_guid` | Opmerkingen en reacties |
| `Opmerkingvermelding` (tabel) | `gebruiker`, `heeft_toegang` | Wie er met @ genoemd is, en of die het bestand mag lezen |
| `Objectuitcheck` | `ruimte`, `bestand`, `gebruiker`, `objecten` (JSON-lijst GlobalIds), `omschrijving` (bijv. "kolommen verdieping 3"), `apparaat`, `verloopt` | Uitchecken per object (§5.7) |
| `Opslagvolger` | `gebruiker`, `doel_doctype` (`Opslagbestand`, `Opslagmap`, `Ruimte`), `doel`, `ruimte`, `soorten` (JSON: welke gebeurtenissen), `bron` (`zelf`, `automatisch`) | Volgen (§5.10) |
| Aanwezigheid | Geen DocType. Redis-sleutel per sessie met een levensduur van 90 s | Wie heeft het bestand nu open (§5.8) |

`Opslagwijziging` krijgt nieuwe soorten: `opmerking`, `opgelost`, `objecten_uitgecheckt`, `objecten_vrijgegeven`,
`samengevoegd`, `teruggezet_naar_versie`. Zo blijft de wijzigingenlijst de ene bron voor activiteit en sync.

### 5.4 Gebeurtenissen: één weg voor alles wat verandert

Nu schrijft elke handeling zelf een regel in de wijzigingenlijst (`_log`). Voorstel: één functie
`gebeurtenis(soort, ruimte, doel_doctype, doel, naam, gegevens)`. Die schrijft de regel in `Opslagwijziging` in
**dezelfde transactie** als de handeling, zoals `_log` nu. Zo bestaat er nooit een versie zonder regel, of een regel
zonder versie. Pas **na de commit** (`frappe.db.after_commit`) doet hij de rest:

1. een push via `frappe.publish_realtime` naar de **persoonlijke kamer** (`room=`, zie hieronder) van elke gebruiker die het
   bestand **op dat moment** mag lezen. De server bepaalt dat bij elke aflevering opnieuw (rol, deling, deellink,
   gearchiveerde ruimte, `buiten_deling`). Een kamer per bestand of per ruimte gebruikt het ontwerp niet: Frappe
   controleert het recht daar alleen bij het abonneren, en een ingetrokken deling haalt niemand uit de kamer (review
   Sol, hele ontwerp, 5 november: na het intrekken bleef het lidmaatschap bestaan). De push bevat alleen wat nodig is
   om iets op te halen (soort, bestand, de `name` van de regel); de inhoud haalt de client met een gewone aanroep, die
   het recht weer controleert. De kosten: één bericht per lezer per gebeurtenis (zie §12 voor veel deelnemers).
   **Geen andere route.** Een bericht over een opslagdocument gaat alleen via `gebeurtenis`, en alleen naar de
   persoonlijke kamer van wie het op dat moment mag lezen. Frappe publiceert zelf ook, buiten `gebeurtenis` om, naar
   kamers waar niemand het recht opnieuw controleert: `doc_update` en `docinfo_update` (kamer van het document),
   `list_update` (kamer van de DocType), `doc_viewers` (kamer van het open formulier), de voortgang van een
   bulkbewerking met de naam van het document (kamer van een taak; abonneren vraagt daar geen recht), en
   `kanban_board_update` (kamer van de hele site, waar elke `System User` in zit). Bronnen:
   `frappe/model/document.py` r. 1737–1785, `frappe/realtime/handlers.py` r. 35, 60 en 118,
   `desk/doctype/bulk_update/bulk_update.py` r. 125, `desk/doctype/kanban_board/kanban_board.py` r. 654 (reviews
   Sol, hele ontwerp, rondes 2 en 3). Het ontwerp sluit dat niet per kamer, maar op drie plekken:
   - **Bij de bron.** De DocTypes van de opslag veranderen alleen via de API van openaec-storage. De controller
     weigert elke andere schrijfroute (formulier in de desk, bulkbewerking, Kanban, data-import, `frappe.client`,
     `/api/resource`), zoals §5.14 vraagt; alleen patches en de beheerder van de site gaan eromheen. Een
     standaardroute komt dus niet tot opslaan, en dus niet tot publiceren. Maar Frappe reageert bij elk opslaan
     ook zelf, ook via de eigen API en los van `notify_update`: `Document.run_method` draait meldingsregels
     (`Notification`), `Webhook`s, `Server Script`s bij een DocType-gebeurtenis en automatiseringen (`Automation
     Flow`) (`frappe/model/document.py` r. 1531–1534). Een meldingsregel op `Opslagbestand` met de maker als
     ontvanger geeft zo een `Notification Log` aan Anna, ook nadat zij haar leesrecht verloor (review Sol, ronde
     4). Wie zo'n regel mag maken, bepaalt hoe het ontwerp dit sluit. Er zijn twee groepen (rechten volgens de
     DocTypes in de bench):
     - **Wat een gewone gebruiker kan** (`Desk User`, een rol in een ruimte, een gast): een `Kanban Board`, `ToDo`,
       `Comment`, Frappe-`DocShare`, `Document Follow` of `Auto Repeat` maken. Die zijn dicht. De opslag weigert
       ze met een DocType van de opslag als doel (één functie in `doc_events`, bij `validate`, met per soort het
       veld dat naar de DocType wijst). Alle DocTypes van de opslag houden `track_changes` uit, ook `Overdracht`
       (dat staat nu aan, maar niets leest die geschiedenis): dan maakt Frappe er geen `Version` voor, en volgt
       niemand er een automatisch na het aanmaken (`frappe/desk/form/document_follow.py` r. 54). Zo kan de
       weigering van `Document Follow` ook geen overdracht laten mislukken (review Sol, ronde 5).
     - **Wat alleen een beheerder van de site kan** (`System Manager`, of `Script Manager` voor een `Server
       Script`): `Notification`, `Webhook`, `Server Script`, `Automation Flow`, `Assignment Rule`, `Milestone
       Tracker` en `Workflow`. Die rollen lezen nu al elke tabel van de site: `System Console` voert code uit met
       `read_sql` (`frappe/desk/doctype/system_console/system_console.py` r. 29–33), en een `Server Script` net
       zo. Tegen wat zij instellen, beschermt geen regel in een app. Hier ligt dus de grens van het vertrouwen:
       een beheerder van de site is vertrouwd voor alle data van de site, net als bij elke andere app in Frappe.
       Een regel van een beheerder op een record dat gegevens doorgeeft (bijv. een meldingsregel op `Notification
       Log` of `Document Follow`, review Sol, ronde 5) valt onder die grens. De opslag doet wel twee dingen tegen
       vergissingen: hij weigert zo'n regel als die direct op een DocType van de opslag reageert (dezelfde
       functie), en de patch bij de invoering zet bestaande regels van die soorten voor de opslag uit, haalt hun
       ingeplande taken uit de wachtrij voordat de werkers weer starten, en logt elke regel.
     - **Wat Frappe uitvoert met andere rechten dan die van wie het start** (review Sol, ronde 6). Hier telt niet
       het doel, maar wiens rechten gelden. Er zijn twee soorten:
       - `Auto Email Report` voert een rapport uit als de gebruiker in het veld `user` ("Based on Permissions For
         User"), en de maker kiest die vrij (`frappe/email/doctype/auto_email_report/auto_email_report.py` r. 152,
         `frappe/core/doctype/report/report.py` r. 312). `Report Manager` mag het maken, en die rol is geen
         beheerder. Anna heeft `Desk User` en `Report Manager`, maar geen leesrecht op F1. Zij maakt een `Report
         Builder` op `Opslagbestand`, kiest `user = Administrator` en zichzelf als ontvanger, en krijgt F1 via
         `download` of `send_now`. Met een rapport op `Notification Log` werkt het net zo. De patch bij de
         invoering sluit dit in vier stappen:
         1. De rechten op `Auto Email Report` zijn daarna alleen voor `System Manager` (`Custom DocPerm`).
            `Report Manager` verliest lezen, schrijven, maken en delen, dus ook de lijst.
         2. Elke `DocShare` op een `Auto Email Report` gaat weg. Frappe kijkt na een weigering nog naar een
            deling, ook voor de lijst (`frappe/permissions.py` r. 200–209); `download` en `send_now` controleren
            alleen die toegang, niet of het rapport aan staat (r. 303–323).
         3. Alle bestaande rapporten gaan uit, ook die van een beheerder: een `Report Manager` kon vóór de
            invoering elk rapport wijzigen (`user`, ontvangers), en de scheduler controleert bij het uitvoeren
            geen rechten (r. 342). De ingeplande taken gaan uit de wachtrij voordat de werkers weer starten. De
            patch logt elk rapport.
         4. In de eerste versie blijven ze uit: de opslag weigert `enabled = 1` bij `validate` (in dezelfde functie
            in `doc_events`). Een rapport leest zijn definitie (`Report`) bij elke uitvoering opnieuw, en een
            `Report Manager` mag een `Report Builder` van een beheerder wijzigen (`frappe/desk/reportview.py`
            r. 392). Anna haalt dan het filter op haar ruimte uit de definitie, en het rapport dat een beheerder
            goedkeurde, mailt haar F1 als `Administrator` (review Sol, ronde 8). Een rapport kan pas weer aan als
            ook de definitie beschermd is; dat staat als optie in §9.

         Een `has_permission`-haak is niet genoeg: hij dekt de lijst niet en wijkt voor een bestaande deling
         (review Sol, ronde 7). `user` aan de maker binden is ook niet genoeg: dan kan een `Report Manager` nog
         een rapport van een beheerder downloaden dat als `Administrator` draait.
       - Een voorbereid rapport (`Prepared Report`) bewaart de uitkomst van een rapport, gemaakt met de rechten van
         wie het aanvroeg (`frappe/core/doctype/prepared_report/prepared_report.py` r. 127). Wie `Prepared Report
         User` heeft, leest elke uitkomst van een rapport dat hij mag gebruiken, ook een uitkomst die
         `Administrator` maakte, met de bijlage erbij (r. 365–385; `frappe/core/doctype/file/file.py` r. 1105).
         Anna mag rapport R gebruiken maar F1 niet lezen; een uitkomst van R van `Administrator` geeft haar F1
         (review Sol, ronde 8). De patch bij de invoering sluit dit net als bij `Auto Email Report`: de rechten
         op `Prepared Report` zijn daarna alleen voor `System Manager` (`Custom DocPerm`), en elke `DocShare` op
         een `Prepared Report` gaat weg. De bijlagen volgen het document, dus die zijn dan ook dicht. Wat het
         kost: een rapport dat Frappe in de achtergrond voorbereidt (een groot rapport, vooral in ERPNext), is in
         de eerste versie alleen voor beheerders. "Alleen je eigen uitkomst" is een optie voor later (§9).
       - Een sjabloon (Jinja) krijgt bij het renderen leestoegang tot elke tabel, zonder rechtencontrole:
         `frappe.db.get_all` met `ignore_permissions`, `frappe.db.get_value` en `frappe.db.sql` voor leesquery's
         (`frappe/utils/safe_exec.py` r. 306–311, 351–353 en 580–589, ook met `restrict_globals`). Wie een
         sjabloon mag schrijven dat Frappe rendert, leest dus alle gegevens van de opslag: namen, paden,
         opmerkingen, meldingen, en ook het token van een deellink (`Opslagdeling.token`). Met dat token geeft
         `openaec_storage/delen.py` (r. 153) een downloadlink, dus ook de inhoud van de bestanden ligt binnen
         bereik (review Sol, ronde 7). In de bench
         kunnen `System Manager` (`Print Format`, `Email Template`, `Notification`, `Web Template`) en `Website
         Manager` (`Web Page`, `frappe/website/doctype/web_page/web_page.py` r. 116–130) zo'n sjabloon schrijven. Met ERPNext komen er rollen
         bij. Zo zijn het onderwerp en het bericht van `Auto Repeat` een sjabloon, en geeft
         `generate_message_preview` het resultaat direct terug (`frappe/automation/doctype/auto_repeat/
         auto_repeat.py` r. 424, 461 en 646–656). `Accounts User` en `Accounts Manager` mogen `Auto Repeat`
         maken, maar alleen voor een DocType met `allow_auto_repeat`. De bench heeft er geen; ERPNext wel. Dat
         laatste is afgeleid, niet getoetst. Een app kan dit niet sluiten zonder Frappe te veranderen. Elke rol
         die sjablonen mag schrijven, hoort dus bij de grens van het vertrouwen, net als `System Manager`.

     Een planner, modelleur of gast krijgt dus nooit een van deze beheerdersrollen, en ook geen rol die sjablonen
     mag schrijven; dat staat bij A4. Krijgen gewone medewerkers in de kern zo'n rol, dan is de keuze "Een eigen
     site voor de opslag" (§9) nodig. De opslag
     gebruikt geen van deze soorten zelf: delen gaat via de eigen rechten (§5.14), opmerkingen via
     `Opslagopmerking` (K6), meldingen via `gebeurtenis` (§5.10). De lijsten volgen Frappe zoals in de bench;
     welke soorten de kern heeft, staat bij A2.
   - **In de eigen API.** De controllers hebben een lege `notify_update`: opslaan via de eigen API geeft geen
     `doc_update` en geen `list_update`. De andere reacties van Frappe (meldingsregels, webhooks, scripts,
     automatiseringen) hangen af van configuratie; die is voor gewone gebruikers dicht en voor beheerders de grens
     van het vertrouwen (hierboven). `gebeurtenis` geeft de kamer zelf op (`room=` de persoonlijke kamer), niet `user=`: met een
     `task_id` in de context kiest `publish_realtime` anders de kamer van de taak (`frappe/realtime/__init__.py`
     r. 66–73).
   - **Bij abonneren**, als tweede slot. De realtimeserver vraagt bij `doc_subscribe`, `doc_open` en
     `doctype_subscribe` de controle `frappe.realtime.has_permission`. Die vervangt openaec-storage via
     `override_whitelisted_methods` in `hooks.py`: voor een DocType van openaec-storage antwoordt hij altijd nee, voor
     elke andere DocType roept hij de gewone controle aan (Sol bevestigde in ronde 3 dat dit werkt voor de aanroep
     van de realtimeserver). Een weigering haalt niemand uit een kamer waar hij al in zat, en `doc_close` stuurt
     `doc_viewers` zonder controle (`frappe/realtime/handlers.py` r. 103). Daarom herstart de invoering de
     realtimeserver: alle verbindingen beginnen opnieuw, en niemand zit daarna nog in een kamer van de opslag.

   Een formulier in de desk krijgt voor deze DocTypes dus geen live bijwerking; dat is goed zo. Bewijs: een
   ontvanger zonder recht zit in elke soort kamer (site, taak, document, DocType, open formulier). Na een handeling
   via elke standaardroute (die geweigerd wordt) en via de eigen API ontvangt hij niets over het document; een lezer
   ontvangt alleen in zijn persoonlijke kamer. Ook in de proef: een meldingsregel op `Opslagbestand` met de maker als
   ontvanger, gemaakt vóór de invoering, met een taak die al in de wachtrij stond; na de invoering en een upload van
   Piet krijgt Anna (geen leesrecht meer) geen `Notification Log`. Per soort uit beide groepen: aanmaken met een
   opslag-DocType als doel wordt geweigerd. En een `Overdracht` maken met `follow_created_documents` aan slaagt,
   zonder `Document Follow`. Anna met `Report Manager` maakt een `Auto Email Report` met `user = Administrator`,
   een keer op `Opslagbestand` en een keer op `Notification Log`. Maken, `download` en `send_now` worden
   geweigerd, en de lijst toont haar geen rapporten. Vóór de invoering zette Anna een rapport van een beheerder
   op `user = Administrator` met zichzelf als ontvanger, en deelde ze een eigen rapport met zichzelf. Na de
   invoering staan alle rapporten uit, de scheduler verstuurt niets, en `download` en `send_now` worden ook op
   het gedeelde rapport geweigerd. Daarna kan niemand een automatisch rapport aanzetten, ook een beheerder niet. Anna met
   `Prepared Report User` ziet geen uitkomst die `Administrator` maakte, ook niet de bijlage, niet in de lijst
   en niet via een deling van vóór de invoering. De proef schrijft ook de rollen op die in de site een sjabloon
   mogen schrijven, en legt die lijst naast A4. Dit hangt af van A2 (§9): welke realtimeserver de kern draait, welke controle die bij abonneren vraagt,
   en welke soorten configuratie op een document reageren;
2. de haak `openaec_storage_gebeurtenis` uit `hooks.py` van andere apps, in de wachtrij (issue #7). Een fout in die
   app houdt de handeling niet tegen;
3. meldingen aan de volgers (§5.10), gebundeld.

Valt het proces uit na de commit, dan is de push of de melding weg, maar de regel staat er. Wie niets mag missen
(een andere app via de haak, de sync), leest de wijzigingenlijst vanaf zijn cursor. De push en de haakaanroep geven
de `name` van de regel mee; het commitnummer (hieronder) heeft de regel op dat moment nog niet, dus de ontvanger haalt
het op met `wijzigingen()`. Elke weg komt zo langs dezelfde plek: browser, desktop-app, batch, WebDAV en
uploadlink.

**De cursor mag niet voorbij een open transactie schuiven.** Frappe deelt het volgnummer uit bij het invoegen, via
een sequence zonder cache (`frappe/database/sequence.py`), dus in de volgorde van invoegen en niet van committen.
Voorbeeld: transactie A krijgt 41 en blijft open, B krijgt 42 en commit. Een client leest 42 en bewaart cursor 42.
Daarna commit A. `wijzigingen(sinds=42)` vraagt `name > 42` (`api.py` r. 858) en ziet 41 nooit. Dit raakt de
desktop-sync nu al; alleen de volledige ronde elke 10 minuten vangt het op.

Oplossing (stap 4b): een **commitnummer, uitgedeeld bij het lezen**. `Opslagwijziging` krijgt een veld
`commitnummer`; een nieuwe regel krijgt 0. Wie de lijst leest (`wijzigingen()`, `activiteit()`, en straks push en
haak), veegt eerst, in een eigen korte transactie: hij neemt het slot op één tellerregel voor de site, zoekt daarna
met een gewone lezing alle vastgelegde regels met nummer 0, geeft ze in volgorde van `name` de volgende nummers, werkt
de teller bij en commit. Pas daarna leest hij op `commitnummer`.

Waarom dat veilig is: een regel krijgt pas een nummer als hij al vastgelegd is, en altijd een hoger nummer dan elk
nummer dat eerder is uitgedeeld. Een client heeft alleen nummers gezien die al uitgedeeld waren. Een regel die later
zichtbaar wordt, krijgt dus een hoger nummer dan de cursor van elke client, en niemand mist hem. Langs welke weg de
regel is vastgelegd, maakt niet uit: een gewone commit, een impliciete commit door DDL, een rauwe SQL-commit of een
achtergrondtaak. De schrijver hoeft niets te doen. Een eerdere opzet nummerde vlak voor de commit, in
`frappe.db.before_commit`; twee rondes van Sol op PR #16 vonden steeds een commitroute buiten die haak om, of een
opruimhaak die werk van de volgende transactie wiste. Die opzet is daarom vervallen.

De volgorde van de veger, precies:

1. Een gewone lezing in de lopende transactie: is er een regel met nummer 0? Nee: klaar, geen slot.
2. Ja: commit de lopende transactie. `wijzigingen()` en `activiteit()` schrijven zelf niets, dus die is leeg. Staat er
   toch schrijfwerk in (`frappe.db.transaction_writes`), dan weigert de veger: dat is een programmeerfout. Frappe zet
   die teller ook bij een rollback naar een savepoint op 0, dus deze controle is een vangnet, geen garantie.
3. Het slot op de tellerregel (`SELECT … FOR UPDATE`). Twee vegers wachten hier op elkaar.
4. Een gewone (niet-vergrendelende) lezing van de regels met nummer 0. De momentopname begint pas hier, na het slot,
   dus de veger ziet de nummers van de vorige veger. Hij ziet alleen vastgelegde regels: een regel van een open
   transactie blijft 0 en krijgt in een latere ronde een hoger nummer. De veger wacht zo nooit op een schrijver.
5. Elke regel krijgt in volgorde van `name` het volgende nummer (`UPDATE … WHERE name = … AND commitnummer = 0`), en
   de teller gaat mee omhoog.
6. `frappe.db.commit()`, vóór de methode antwoordt. Frappe commit na een GET-verzoek niet vanzelf, maar draait terug
   (`frappe/app.py` r. 445-448). Zonder deze commit krijgt de client nummers die daarna vervallen, en de volgende
   veger geeft die nummers aan andere regels.
7. Pas dan de gewone lezing op `commitnummer > sinds`. Regels met nummer 0 vallen daar altijd buiten.

Bij fout 1020 of 1213 (`innodb_snapshot_isolation`, deadlock) begint de veger een paar keer opnieuw bij stap 2.

- `wijzigingen()` filtert en sorteert op `commitnummer` in plaats van `name`. `volgnummer` en `cursor` in het antwoord
  worden het commitnummer. De cursor is de laatste regel van de pagina vóór het filter op een deling (zoals nu), en
  bij een lege pagina gewoon `sinds`. De losse vraag naar het hoogste nummer van de site (`api.py` r. 867) vervalt:
  tussen de lege pagina en die vraag kan een commit vallen, en die regel zou de client dan nooit krijgen. Paginering
  en `meer` blijven zoals nu.
- `ruimtebeheer.activiteit` sorteert en nummert ook op het commitnummer, zodat één gebeurtenis in beide API's
  hetzelfde nummer heeft.
- Bestaande regels krijgen bij de migratie `commitnummer = name`, en de teller begint boven het hoogste `name`. Een
  cursor die een client nu bewaart, blijft dus geldig.
- Een gat in de nummers binnen één ruimte is gewoon een regel van een andere ruimte. Dat geeft niets: de veiligheid
  komt uit de commitvolgorde, niet uit het zoeken naar gaten.
- Verplichte databaseproeven: twee transacties in omgekeerde volgorde gecommit (de client mist niets), een
  teruggedraaide transactie, regels in twee ruimtes, een deling als wortel, een volle pagina (de cursor schuift op),
  een lege pagina met een commit vlak erna, een taak die tussendoor commit, een batch waarvan één item naar een
  savepoint terugging, een rauwe `frappe.db.sql("commit")` gevolgd door een rollback, een `after_rollback`-callback
  die een regel schrijft, een mislukte DDL na een savepoint (`RENAME TABLE`, `ALTER TABLE`), een GET-verzoek via de
  echte HTTP-laag (de nummers blijven na het verzoek staan), twee vegers tegelijk (geen dubbel nummer) en een veger
  terwijl een schrijftransactie open staat (de veger wacht niet; de regel krijgt na zijn commit een hoger nummer). In
  elk geval verschijnt elke vastgelegde regel precies één keer in `wijzigingen(sinds=0)`, en nooit onder een cursor
  die een client al kreeg.
- Sloten: alleen de veger neemt het tellerslot, en hij wijzigt alleen vastgelegde regels met nummer 0, die geen
  schrijver meer aanraakt. Een schrijver neemt het tellerslot nooit. Er is dus geen kring van wachtende transacties
  tussen schrijvers en vegers.

### 5.5 API-overzicht

Alles onder `/api/method/openaec_storage.samenwerken.<onderwerp>.<methode>` (één module per onderwerp: `versies`,
`opmerkingen`, `aanwezigheid`, `meldingen`, `uitchecken`, `rechten`), met de inlog van de gebruiker. Rechten volgen
`vereis_op_plek`, dus ook delingen. Namen zijn een voorstel.

| Methode | Recht | Wat |
|---|---|---|
| `meld_aanwezig(bestand, modus, app?, apparaat?, sessie?)` | lezen | Ik heb het bestand open (`bekijken` of `bewerken`). Elke 30 s herhalen. Geeft `sessie` en de lijst van aanwezigen |
| `meld_weg(sessie)` | — | Ik sluit het bestand |
| `aanwezig(bestand)` | lezen | `[{gebruiker, naam, modus, app, apparaat, sinds}]` |
| `opmerkingen(bestand, status?, versie_id?)` | lezen | Draden met reacties; per draad ook `anker_status` (§5.9) |
| `plaats_opmerking(bestand, tekst, anker?, versie_id?, draad?)` | lezen (zie keuze K7) | Nieuwe opmerking of reactie; @noemen in de tekst als `@gebruiker@voorbeeld.nl` |
| `wijzig_opmerking`, `verwijder_opmerking` | eigen opmerking | — |
| `los_op(draad)`, `heropen(draad)` | lezen | Status van een draad |
| `volg(doel_doctype, doel, soorten?)`, `ontvolg(…)`, `mijn_volgers()` | lezen | Volgen |
| `checkout_objecten(bestand, objecten, omschrijving?, uren?)` | schrijven | Uitchecken per object; weigert met `object_uitgecheckt` en de lijst als een ander er al een heeft |
| `geef_objecten_vrij(bestand, objecten?)` | eigen uitcheck (of beheerder) | Alles of een deel vrijgeven |
| `uitgecheckte_objecten(bestand)` | lezen | `[{gebruiker, objecten, omschrijving, verloopt}]` |
| `zet_terug(bestand, versie_id, basis_versie_id)` | schrijven | Nieuwe versie die gelijk is aan een oude (kopie in S3, geen upload) |
| `verschil(bestand, van_versie, naar_versie)` | lezen | Verschil per object (§5.6); later, als de server de bibliotheek kan draaien |
| `activiteit(bestand, start?, aantal?)` | lezen | Versies, opmerkingen, sloten en delingen van één bestand, nieuwste eerst |

Uitbreiding van de bestaande API:

- `upload_start(…, basis_bestand?, basis_versie_id?, bij_conflict="kopie" | "weiger", samengevoegd_uit?)`. Een versienummer is alleen
  uniek binnen één bestand. Daarom vergelijkt de server het paar (bestand, versie): `basis_bestand` is het id van het
  bestand waarop de client werkte. Voorbeeld: Anna werkt offline aan `planning.ifc` (bestand F1, versie 1). Piet
  verwijdert F1 en maakt onder dezelfde naam een nieuwe planning (F2, ook versie 1). Nu zoekt `_registreer` op naam
  (`_actief_bestand`, `api.py` r. 90) en vergelijkt alleen het nummer, dus Anna's oude planning wordt F2 versie 2,
  zonder conflict. Met `basis_bestand=F1` ziet de server dat op die plek een ander bestand staat: Anna's basis hoort
  niet bij F2. Met `weiger` geeft `upload_klaar` dan een fout in plaats van een conflictkopie: `onbekende_basis` bij
  een ander bestand, `verouderde_basis` bij een andere versie van hetzelfde bestand (zie het volgende punt), met het
  huidige bestand en de huidige versie. Met `kopie` wordt het in beide gevallen een conflictkopie. Zonder
  `basis_bestand` blijft de vergelijking zoals nu, dus bestaande clients merken niets. De vergelijking gebeurt onder
  het slot van stap 4, dus atomair.
- **De basis is de versieregel zelf, niet haar nummer.** Een versienummer bleek geen veilige identiteit (§2; reviews
  van Sol, rondes 10 tot en met 13). Het kan terugkomen: verplaats een bestand met overschrijven, herstel het en
  upload opnieuw, en "F1 versie 2" wijst naar andere inhoud. Twee verplaatsingen tegelijk naar hetzelfde doel geven
  twee regels hetzelfde nummer, en een regel kan zelfs van het ene doel naar het andere schuiven. Welke nummers dat
  vóór de invoering raakte, is achteraf niet vast te stellen. Daarom is de basis het id van de versieregel
  (`Bestandsversie.name`, hier `versie_id`). Dat id is om twee redenen veilig, allebei nagelopen in de code en de
  geschiedenis van `openaec_storage/`:
  1. Het komt nooit terug: het is een willekeurige hash als primaire sleutel (`autoname: hash`), en geen code wist
     een versieregel.
  2. De inhoud waar een versieregel naar wijst, verandert nooit: `s3_sleutel` wordt nooit vervangen, en nieuwe inhoud
     krijgt altijd een nieuwe regel met een nieuwe sleutel. Wel mag `verplaats` het bestand en het volgnummer van een
     regel aanpassen, en het dossier de revisiegegevens (`revisie`, `revisiestatus`, `dossier.py` r. 432). Die raken
     de inhoud niet. Nu kan een schrijver van de ruimte een versieregel nog via de algemene routes van Frappe wijzigen
     of wissen (`/api/resource`, `frappe.client.set_value` en `save`, de wijzigroute van API v2). Stap 4 sluit dat af
     in de controller van de DocType: een bestaande versieregel opslaan of wissen weigert altijd. Een
     `has_permission`-haak alleen is niet genoeg, want Frappe kijkt na een weigering nog naar een expliciete deling
     (`DocShare`), en die kan al bestaan (review Sol op PR #9). De server zelf wijzigt een regel alleen met
     `frappe.db.set_value`, dat de controller niet aanroept. Een negatieve test per route, ook met een schrijvende
     deling en als Administrator.

  Wat er verandert:
  - `Opslagbestand` krijgt `huidige_versie_id`: de regel die nu de inhoud is. De server zet hem overal waar hij
    `huidige_versie` zet.
  - `upload_start(…, basis_bestand, basis_versie_id, …)`: onder het slot vergelijkt de server `basis_bestand` met het
    bestand op de plek en `basis_versie_id` met zijn `huidige_versie_id`. Klopt het, dan komt er een nieuwe versie.
    Hoort de basisregel bij dit bestand maar is ze niet meer de huidige, dan `verouderde_basis`, met het huidige
    bestand, nummer en `versie_id`. Hoort ze niet (meer) bij dit bestand (een ander bestand op de plek, of de regel
    ging met een verplaatsing naar een ander bestand), dan `onbekende_basis`. Met `bij_conflict=kopie` wordt het in
    beide gevallen een conflictkopie. Zonder `basis_versie_id` blijft de vergelijking op `basis_versie`, zoals nu:
    oude clients houden hun gedrag, ook het risico van een teruggekomen nummer; de desktop-sync van stap 5 stuurt
    altijd `basis_versie_id`.
  - `download(bestand, versie_id=…)` geeft de inhoud van precies die regel, of `onbekende_basis` als ze niet (meer)
    bij het bestand hoort. `lees(basis)` geeft dus altijd de inhoud waar de client vanuit werkte, of een duidelijke
    fout.
  - Lijsten, `wijzigingen()`, `versies()`, `download()` en het antwoord van een upload geven `versie_id` mee. Het
    volgnummer blijft, voor mensen. Elk contract dat naar een versie verwijst (`samengevoegd_uit`, `zet_terug`, het
    anker van een opmerking, de regel van een nieuwe versie in de wijzigingenlijst), gebruikt `versie_id`. Een bron die verplaatst is of niet meer
    toegankelijk, geeft een duidelijke fout; er is geen terugval op het nummer.
  - **Ook verwijderen is voorwaardelijk.** Een client die een bestand op de server verwijdert omdat het lokaal weg
    is (de sync, ook uitgesteld of na offline werken), stuurt de versie mee waarvan hij uitging:
    `verwijder(bestand, basis_versie_id)`. De server controleert dat onder hetzelfde slot als een upload. Is de
    huidige versie een andere, dan blijft het bestand staan en antwoordt de server `verouderde_basis`; de client
    vergelijkt opnieuw (meestal: de nieuwe versie downloaden, want een ander werkte verder). Voorbeeld: B wist F1
    lokaal en zijn scan ziet F1 op V1; daarna slaat A V2 op; B's verwijdering wordt geweigerd en F1 met V2 blijft in
    het actieve dossier. Er is één verwijderroute, zonder onderscheid naar herkomst: ook de browser stuurt de versie
    die hij toonde. Was die verouderd, dan haalt de browser eerst de huidige stand op, en pas daarna kan de gebruiker
    opnieuw bevestigen (review Sol, hele ontwerp, ronde 2: B had de lijst met F1 op V1 open, A sloeg V2 op, en B's
    oude bevestiging mag V2 niet wegzetten). Bij een map of een bulkactie stuurt de client per bestand de versie die
    hij toonde; een bestand dat intussen veranderde of dat de client niet zag, blijft staan, en de map met hem. Alles
    wat een huidige versie wegzet, volgt dezelfde regel: `verplaats(…, overschrijf=1)` stuurt ook de versie van het
    doel die de client zag. Zolang de server dit niet
    kent (de server ligt stil, §9), haalt de sync vlak voor het verwijderen de huidige versie opnieuw op en stopt bij
    een verschil. Dat verkleint het gat, maar sluit het niet (review Sol, hele ontwerp, 5 november).
  - `verplaats` leest het doel met een slot, zodat twee verplaatsingen tegelijk geen dubbel nummer meer uitgeven. Een
    nummer kan na verplaatsen, herstellen en opnieuw uploaden nog terugkomen. Dat is verwarrend voor mensen, maar niet
    meer onveilig. `zet_terug` maakt altijd een nieuwe versie.

  **Invoering.** Er wordt niets hernummerd en er komt geen kopie in S3. De patch zet per bestand `huidige_versie_id`
  op de regel met `volgnummer = huidige_versie`. Zijn dat er meer (twee verplaatsingen tegelijk), dan wordt de laatst
  gewijzigde de huidige; de andere krijgen een vrij nummer boven het hoogste van het bestand, zodat elk nummer weer
  één regel aanwijst. Is er geen (de regel schoof naar een ander bestand), dan wordt de hoogste bestaande regel van het
  bestand de huidige. Heeft het bestand geen regels meer, dan blijft `huidige_versie_id` leeg, en een upload met een
  basis geeft `onbekende_basis`. De patch logt elk van deze herstellingen.
  Een nieuwe client die een bestand kent zonder `versie_id` (uit de tijd vóór de invoering), neemt de `versie_id` van
  de huidige versie alleen over als zijn lokale bestand ongewijzigd is én gelijk aan die versie: na een download, of
  zonder download als de server de blokhashes van die regel geeft en ze gelijk zijn. Wijzigde hij lokaal, dan heeft
  hij geen zekere basis, en dat wordt één conflictkopie. Voorbeeld: Anna haalt F0 versie 2 (inhoud A) op en werkt
  offline met de oude client. Daarna komen A en B door twee verplaatsingen tegelijk allebei als nummer 2 op F0. Na de
  invoering is B de huidige regel (de laatst gewijzigde) en heeft A een vrij nummer. Anna's nieuwe client heeft voor F0
  geen `versie_id` en een gewijzigd bestand: haar werk wordt een conflictkopie, en B blijft staan. Piet haalt F0 op,
  krijgt de `versie_id` van B, en slaat gewoon op.
- `mogelijkheden()` krijgt `samenwerken: {aanwezigheid, opmerkingen, volgen, objectuitcheck, bij_conflict_weiger,
  verschil}` zodat een client weet wat de server kent.

Gebeurtenissen via push. Elke push heeft dezelfde vorm als in §5.4: `{soort, bestand, name}`, zonder inhoud
(`name` is de regel in de wijzigingenlijst; leeg bij aanwezigheid, want die staat alleen in Redis). De client haalt de
inhoud op met de gewone aanroep, die het recht controleert. Soorten (en de aanroep om op te halen):
`bestand.nieuwe_versie` (`wijzigingen`, `versies`), `bestand.aanwezigheid` (`aanwezig`), `bestand.opmerking`
(`opmerkingen`), `bestand.uitgecheckt` en `bestand.vrijgegeven` (slot per bestand), `bestand.objecten_uitgecheckt` en
`bestand.objecten_vrijgegeven` (`uitgecheckte_objecten`).

### 5.6 Samenvoegen

Dit is het deel dat nu gebouwd wordt, als bibliotheek `openaec-samenvoegen` (Rust, in openaec-cloud-desktop).

**IFC lezen.** De bibliotheek leest STEP (ISO 10303-21) zelf, zonder IfcOpenShell. Een object is een instantie van
een subtype van IfcRoot, met de GlobalId als sleutel. Welke typen dat zijn, en welke attributen een SET of een LIST
zijn, komt uit de EXPRESS-schema's van IFC2X3, IFC4 en IFC4X3.

**Inhoud zonder nummers.** Elke knoop krijgt een inhoudshash: type en attributen, met een verwijzing naar een knoop
vervangen door de hash van die knoop, en een verwijzing naar een object door zijn GlobalId. De nummers `#123` doen
niet mee. Dat is nodig, want OPS nummert bij elk opslaan alles opnieuw (`ifcWriter.ts`, `addLine`), terwijl de
GlobalIds gelijk blijven (`issueGuid`). Getallen en strings worden gelijk gemaakt (`1.` = `1.0`).

**Ankers: knopen van een ander object.** Een inhoudshash alleen is niet genoeg. Voorbeeld: verdiepingen P en Q hebben
elk een eigen plaatsing met dezelfde inhoud. Wand W staat relatief aan de plaatsing van P. Ons hangt W onder Q, hun
verplaatst P. Op inhoud lijkt ons gelijk aan de basis, en de herplaatsing van ons zou verdwijnen. Daarom: een knoop
waar een object rechtstreeks naar verwijst (bijv. `ObjectPlacement`, `Representation`) is een *anker* van dat object,
met als identiteit `(GlobalId, attribuut, positie)`. Verwijst een knoop van W naar een anker van een ánder object,
dan telt die verwijzing als het anker, niet als de inhoud. Zo is "W hangt nu onder Q" een wijziging van W.

Een knoop die meer objecten rechtstreeks gebruiken (IFC staat gedeelde plaatsingen toe), heeft als identiteit de
gesorteerde verzameling van al zijn ankers. Er is geen gekozen vertegenwoordiger. Splitst of deelt een kant zo'n
knoop anders, dan verandert die identiteit, en telt het als een wijziging van die kant. Wijzigde de andere kant
hetzelfde attribuut, dan is het een conflict. Kan de schrijver een gedeeld anker niet eenduidig naar één knoop in het
resultaat vertalen, dan is het ook een conflict.

**Ankers in een SET.** Een SET heeft geen volgorde, dus de plek in de tekst zegt niets over welk lid welk is.
Voorbeeld: het project heeft twee contexten met gelijke inhoud, #20 en #200; de wand gebruikt #20, het raam #200. Hangt
ons de wand onder #200 en staan de twee in de tekst omgekeerd, dan mag de wijziging van de wand niet verdwijnen. De
bibliotheek koppelt de leden van zo'n SET tussen twee versies alleen als dat eenduidig kan:

1. op gelijke inhoud, als die inhoud aan beide kanten precies één keer voorkomt;
2. daarna per type, voor de leden die over zijn: het paar met de meeste gelijke attributen en dezelfde objecten die
   ernaar verwijzen, alleen als elk van beide de enige beste keuze van de ander is.

Een lid dat zo niet te koppelen is, geldt aan de ene kant als nieuw en aan de andere als weg. Elk object dat ernaar
verwijst, telt dan als gewijzigd; bij samenvoegen geeft dat een conflict als beide kanten zo'n object raken. Een andere
volgorde kan zo nooit een wijziging verbergen; hooguit meldt de bibliotheek een wijziging te veel. Twee gewijzigde
contexten, of een gewijzigde naast een nieuwe, wijzigen alleen het project als de koppeling eenduidig is.

**Vluchtige velden.** Sommige velden veranderen bij elk opslaan, ook als niemand iets aan het object deed: in
`IfcOwnerHistory` de tijdstempels en de velden over de laatste wijziging, in de HEADER de tijdstempel van
`FILE_NAME`, en `IfcWorkControl.CreationDate` (OPS schrijft bij het hoofdwerkplan en -schema elke keer "nu",
`ifcWriter.ts` r. 452-459). Zo'n veld maakt een object niet "gewijzigd" en geeft nooit een conflict. Een wijziging
ervan gaat wel niet verloren: veranderde alleen hun het veld, dan krijgt het resultaat de waarde van hun; veranderden
beide, dan die van ons. `verschil` meldt zo'n wijziging apart, als vluchtig. Voorbeeld: een baseline in OPS houdt haar
eigen `CreationDate` (`ifcWriter.ts` r. 474). Zet hun die op februari en ons niet, dan staat februari in het
resultaat. De schemadeclaratie (`FILE_SCHEMA`) is geen vluchtig veld: een ander schema is een wijziging, en bij
samenvoegen een conflict. De rest van de keten telt wel mee. OPS bewaart de auteur en de organisatie van een planning in `IFCPERSON` en `IFCORGANIZATION` achter
`IfcOwnerHistory` (`ifcWriter.ts` r. 388, `ifcReader.ts` r. 1397). Een wijziging daarvan mag niet verloren gaan.

**Verschil.** Per GlobalId: toegevoegd, verwijderd of gewijzigd, en bij gewijzigd per attribuut de oude en nieuwe
waarde.

**Samenvoegen (three-way), per object:**

| Situatie | Uitkomst |
|---|---|
| Alleen ons of alleen hun wijzigde, voegde toe of verwijderde | Die kant |
| Beide wijzigden, andere attributen | Beide wijzigingen |
| Beide wijzigden hetzelfde attribuut op dezelfde manier | Die waarde |
| Beide wijzigden een SET van verwijzingen (bijv. de wanden in een verdieping) | Samen: wat erbij kwam en wat eraf ging, van beide kanten |
| Beide wijzigden een eigenschappenset | Per eigenschap (op naam); twee andere waarden voor dezelfde eigenschap = conflict |
| Beide wijzigden hetzelfde gewone attribuut of een LIST anders | Conflict |
| De ene verwijderde, de andere wijzigde | Conflict |
| Na samenvoegen wijst iets naar een verwijderd object | Conflict |
| Na samenvoegen heeft een object of een knoop zonder GlobalId meer verwijzingen dan het schema toestaat (een INVERSE met bovengrens 1, bijv. een wand in twee verdiepingen, of een geometrie met twee stijlen), terwijl dat in ons en in hun elk klopte | Conflict |
| Beide kanten vervingen dezelfde relatie door een nieuwe, met een andere GlobalId (bijv. elk een eigen `IfcRelAssociatesMaterial` voor W en V) | Conflict: welke nieuwe relatie bij welke hoort, is niet vast te stellen |
| Na samenvoegen voldoet een samengevoegde verzameling niet aan de grenzen uit het schema (bijv. ons haalt A en hun haalt B uit `RelatedElements = {A, B}`: leeg, terwijl minstens 1 nodig is) | Conflict |
| Een definitie gold in de basis voor een object, de ene kant haalde hem weg (of verwijderde het object), en de andere kant wijzigde die definitie terwijl hij daar nog voor het object gold (zie de koppelregel hieronder) | Conflict |
| Na samenvoegen zit er een kring in de boom (`IfcRelAggregates`, `IfcRelNests`, `IfcRelContainedInSpatialStructure`), of is een object dat in ons én in hun vanaf het project bereikbaar was, dat niet meer | Conflict |
| Na samenvoegen zit er een kring in de volgorde van taken (`IfcRelSequence`): ons legt A→B, hun B→A. Elke kant is apart berekenbaar; samen kan de planning niet meer gerekend worden (OPS: `CPMSolver.ts` geeft dan een fout zonder berekende taken) (review Sol, hele ontwerp, 5 november) | Conflict |
| Na samenvoegen zit er een kring in een plaatsingsketen (`IfcLocalPlacement.PlacementRelTo`): ons plaatst P ten opzichte van Q, hun Q ten opzichte van P | Conflict |
| Onbekend schema of een geval dat de bibliotheek niet kent | Conflict |

**De koppelregel.** Per object X en per versie bepaalt de bibliotheek de *definities die voor X gelden*:
eigenschappensets, typen, materialen, classificaties en documenten. Een definitie geldt voor X als hij aan X hangt via
`IfcRelDefines*` of `IfcRelAssociates*`, of via een attribuut van een definitie die voor X geldt (bijv. het type van
X, en de `HasPropertySets` van dat type), ook indirect. Directe en indirecte koppelingen vallen zo onder één regel.

Conflict als: een definitie D gold in de basis voor X; bij de ene kant geldt D niet meer voor X (koppeling weg, D uit
de `HasPropertySets` van het type, ander type, of X verwijderd); bij de andere kant geldt D nog voor X, en die kant
wijzigde D. Komen het weghalen en de wijziging van D van dezelfde kant, dan is het geen conflict: die toestand komt
helemaal uit één kant.

Voorbeelden, alle drie conflict:

- Wanden W1 en W2 delen eigenschappenset P. Ons verwijdert W1; hun zet de brandwerendheid in P van 30 naar 60. P
  hangt nog aan W2, maar voor W1 haalde ons P weg, terwijl hun P wijzigde.
- W1 en W2 hebben type T, en T heeft eigenschappenset P (`HasPropertySets`). Ons geeft W1 type T2; hun wijzigt P.
- W1 heeft type T met `HasPropertySets = {P, Q}`. Ons haalt P uit T; hun wijzigt P.

De regel is streng: liever een conflictkopie te veel dan een wijziging die stil verdwijnt. Een verdiepingsrelatie (`IfcRelContainedInSpatialStructure`) of een volgorde in een planning
(`IfcRelSequence`) is geen definitie: een andere verdiepingsnaam bij hun en een verplaatste wand bij ons geven samen
geen conflict.

De boomregel vangt herindelingen die elk apart kloppen, maar samen niet. Voorbeeld: gebouw G bevat verdiepingen P, Q
en S. Ons hangt P onder Q, hun hangt Q onder P. Elke kant apart is een goede boom; samen bevat Q P en P Q, en P en Q
hangen niet meer aan het project. De bibliotheek kijkt daarvoor alleen naar de relaties, niet naar geometrie.

Deze gevallen zijn verplichte tests van de bibliotheek, met de voorbeelden uit de reviews van Sol: de wand die onder
een andere verdieping gaat hangen (de herplaatsing van ons blijft), de wand die via twee nieuwe relaties in twee
verdiepingen komt (conflict), de verwijderde wand met zijn eigenschappenset (conflict), de gedeelde eigenschappenset
van W1 en W2 (conflict), de lege `RelatedElements` (conflict), het ander type terwijl de eigenschappenset van het oude type wijzigt
(conflict), P uit de `HasPropertySets` van een behouden type terwijl hun P wijzigt (conflict), P onder Q en Q onder P (conflict), bewust losmaken en wijzigen door één kant (geen
conflict), een gedeelde plaatsing die een kant splitst (geen stil verlies) en een gewijzigde auteur in OPS (de auteur
van hun blijft). Elk geval in IFC2X3, IFC4 en IFC4X3, waar het schema dat toelaat.

Bij een conflict is er standaard geen nieuwe versie maar een rapport per object en attribuut, met de waarden van
basis, ons en hun. De client maakt dan een conflictkopie, zoals nu, en zet het rapport ernaast. Later kan een scherm
per conflict laten kiezen.

**Schrijven.** Het resultaat begint bij de tekst van ons. Alleen regels van gewijzigde objecten veranderen, en wat
van hun komt, krijgt nieuwe nummers achteraan. Zo blijft de tekst zo dicht mogelijk bij die van ons. Bij het
uploaden helpt dat nu nog niet: de opslag vergelijkt de blokken van 8 MiB op hun plek, en één regel die vooraan
langer of korter wordt, verschuift alle blokken erna. In meting 07 (desktop-PR #12) gaan na het samenvoegen van een
bestand van 92 MB alle 11 blokken over het netwerk. Alleen blokken die op inhoud worden hergebruikt, maken de
upload klein; dat staat bij A11 en in hoofdstuk 12. Een knoop zonder
GlobalId (geometrie, stijl, plaatsing) hergebruikt de schrijver alleen als hij bij hetzelfde object hoort; een knoop
met dezelfde inhoud bij een ander object is niet dezelfde knoop. Voorbeeld: W (rood) en V (blauw) hebben elk eigen,
gelijke geometrie; hun maakt V groen. Het resultaat geeft V groen en laat W rood, nooit andersom. Een gelijk
STEP-nummer zegt daarbij niets. De bibliotheek voegt alleen samen zonder automatische keuze bij een conflict (K4): er
is geen modus "ons wint" of "hun wint", want die verliest ook niet-botsend werk van de andere kant.

**De opslaan-lus.** Eén trait met drie methodes: `huidige()`, `lees(versie)` en `schrijf_als(basis, inhoud)`, die
weigert als de basis verouderd is. De basis is het bestand plus het id van zijn versie, niet een versienummer: bij
openaec-storage `basis_bestand` en `basis_versie_id`. Zo'n basis wijst altijd naar dezelfde inhoud of naar niets (§5.5, "De basis is de
versieregel zelf"). De lus: schrijf; is de basis verouderd, lees basis en huidige, voeg samen, schrijf opnieuw met de
nieuwe basis; geef op na enkele pogingen. Hoort de basis niet (meer) bij het bestand, of heeft de client voor dit
bestand geen `versie_id` (uit de tijd vóór de invoering), dan weigert `schrijf_als` met `onbekende_basis` en faalt
`lees(basis)` (bij openaec-storage `download(…, versie_id=…)`) met een duidelijke fout. De lus voegt dan niet samen: de client maakt een conflictkopie, zoals nu. openaec-storage past op die
trait. Een slot van een ander is een aparte weigering: daar helpt samenvoegen niet, en de lus probeert het niet.

De desktop-sync gebruikt de lus alleen bij een server die `bij_conflict="weiger"` kent (`mogelijkheden()`). Bij een
oudere server blijft alles zoals nu: een conflictkopie. Er is geen tussenweg die een conflictkopie achteraf
samenvoegt en opruimt; zo'n opruimactie kan niet veilig vaststellen dat er niets verloren gaat.

**Wat er bewaard blijft.** De versie van hun en de nieuwe, samengevoegde versie staan op de server en zijn terug te
zetten. De eigen invoer van ons heeft bij een geslaagde samenvoeging nooit als losse versie op de server gestaan. De
desktop-sync bewaart die daarom lokaal (in `.openaec/`, de laatste paar per bestand) voordat hij het lokale bestand
door het resultaat vervangt. Lukt samenvoegen niet, dan gaat ons als conflictkopie naar de server, zoals nu.

**Terugzetten in de lokale map.** De gebruiker kan opnieuw opslaan terwijl de sync samenvoegt, en een programma kan
het bestand nog open hebben. Voorbeeld: de sync leest de planning met taak A op 2, de gebruiker zet A op 3 en slaat
op, en de samengevoegde versie (met A op 2) komt op de server. Die versie mag de lokale A op 3 niet wegdrukken.
Daarom:

1. **De opslaan-lus schrijft het lokale bestand niet.** Samenvoegen werkt met een vaste momentopname S van het lokale
   bestand (inhoud en hash, bewaard in `.openaec/`). Na een geslaagde upload van resultaat N legt de sync vast:
   "server op N, lokaal afgeleid van S". Het lokale bestand blijft wat het is.
2. **De volgende ronde beslist met die toestand.** Is het lokale bestand nog S, dan is het een gewone download van N.
   Is het lokale bestand intussen L (de gebruiker sloeg op), dan voegt de sync samen met basis S, ons L en hun de
   huidige serverversie, en schrijft met `schrijf_als` tegen (bestand, `versie_id`) van die serverversie. Is de
   server intussen verder (een derde schreef), dan weigert `schrijf_als` en draait de lus gewoon opnieuw, met
   (bestand, `versie_id`) van de nieuwe serverversie als basis. S is als basis veilig, omdat N alle wijzigingen van S bevat: de desktop-sync voegt alleen
   samen zonder automatische keuze bij een conflict; bij een conflict wordt er niets geschreven maar een
   conflictkopie gemaakt.
3. **Eén veilige routine voor elke lokale vervanging en elke lokale verwijdering**: na samenvoegen, bij de gewone
   download, en bij het lokaal verwijderen van een bestand dat een ander op de server verwijderde. De laatste twee
   hebben dit gat nu al: `motor.rs` r. 660 doet `remove_file` zonder de inhoud opnieuw te controleren, dus werk dat de
   gebruiker tussen de scan en de verwijdering opslaat, verdwijnt. Bij een verwijdering is er geen nieuwe inhoud om te
   plaatsen; de rest van de routine is gelijk:
   - **Journaal eerst.** Vóór het verplaatsen legt de sync de vervanging duurzaam vast in `.openaec/` (geschreven en
     met fsync): het pad, de plek van de bron, de verwachte inhoud (hash), de nieuwe inhoud (versie N) en de stap.
     Bij elke stap werkt hij het journaal bij.
   - **Herstel bij opstarten.** Vóór de gewone vergelijking en vóór elke beslissing over verwijderen maakt de sync elke
     onafgemaakte vervanging uit het journaal af of terug. Ontbreekt het pad en staat de bron er, dan zet hij de bron
     terug (met een bewerking die een bestaand bestand niet vervangt). Zo ziet de vergelijking nooit een "lokaal
     verwijderd" bestand dat alleen halverwege een vervanging was.
   - **Verplaatsen.** De sync verplaatst het lokale bestand naar `.openaec/bron/` (één rename). Lukt dat niet (Windows
     weigert bij een programma dat het bestand zonder `FILE_SHARE_DELETE` open heeft), dan wacht de vervanging tot een
     volgende ronde, en blijft de toestand staan.
   - **Elke bron is beschermd vanaf het begin.** Een verplaatste bron is onverwerkt tot het tegendeel bewezen is. Ze
     valt niet onder het opruimen en de sync bewaakt haar, los van wat een controle op processen zegt.
   - **Plaatsen.** De sync plaatst de nieuwe inhoud met een bewerking die weigert als er op het pad intussen weer een
     bestand staat (een harde link van een tijdelijk bestand, of `renameat2` met `RENAME_NOREPLACE`, `renamex_np` met
     `RENAME_EXCL`, of `MoveFileExW` zonder `MOVEFILE_REPLACE_EXISTING`). De gewone `std::fs::rename` van Rust vervangt
     een bestaand doel en is hier dus niet goed. Weigert de plaatsing, dan blijft het nieuwe bestand van de gebruiker
     staan als lokale wijziging, afgeleid van S.
   - **Afronden.** Pas als geen proces de bron nog open heeft of in het geheugen heeft gekoppeld (Linux: `fd` én `maps`
     in `/proc/<pid>/`; macOS: `proc_pidinfo`; Windows: de Restart Manager, `RmGetList`), volgt een laatste controle
     van de inhoud tegen de momentopname die echt verwerkt is. Is die gelijk, dan mag de bron weg (of als herstelkopie
     blijven). Is ze anders (een programma schreef via een open verbinding, ook als het daarna sloot), dan is dat een
     lokale wijziging, afgeleid van de verwerkte inhoud, en die gaat door dezelfde opslaan-lus. Bij een verwijdering
     bestaat het bestand op de server niet meer; nieuwe inhoud komt dan terug op het pad en gaat als nieuw bestand naar
     de server (of als conflictkopie als daar intussen weer een bestand met die naam staat). Kan de sync niet
     vaststellen dat er geen schrijvers meer zijn, dan blijft de bron bewaakt.
   - **Bewaking van bronnen.** De scanner (`motor.rs` r. 332) en de bestandswachter (`bewaking.rs` r. 18) slaan
     `.openaec/` nu helemaal over. Ze moeten `.openaec/bron/` wel meenemen, of de sync bekijkt die bronnen elke ronde
     apart; een bron neerzetten zonder bewaking beschermt niets.
4. **Verplichte proeven in stap 5:** lezen, de gebruiker slaat opnieuw op, de upload slaagt, de volgende ronde (beide
   wijzigingen in de volgende versie); een programma dat na de inhoudscontrole via zijn al open verbinding schrijft
   en open blijft; een programma dat tussen de inhoudscontrole en de procescontrole schrijft en sluit; een schrijfbare
   geheugenkoppeling; een geweigerde eerste verplaatsing (lokale inhoud en toestand blijven); een bestand dat vlak
   vóór de plaatsing op het pad verschijnt (het blijft staan); de gebruiker slaat op na de scan maar vóór een lokale
   verwijdering (het nieuwe werk blijft en gaat naar de server); een wijziging die alleen in een bron gebeurt, zonder
   gebeurtenis op het oorspronkelijke pad (de sync verwerkt haar); een bestand dat een ander verwijderde en onder
   dezelfde naam opnieuw maakte, terwijl de client offline werkte (de server weigert; er gaat niets van de nieuwe
   inhoud verloren); een bestand dat een ander met overschrijven verplaatste, herstelde en opnieuw uploadde, terwijl
   de client offline werkte; een doelbestand dat vóór de invoering met twee verplaatsingen tegelijk twee keer
   hetzelfde nummer kreeg, en een regel die zo van het ene doel naar het andere schoof; een client van vóór de
   invoering, met en zonder lokale wijziging (in al deze gevallen wordt de oude basis geweigerd, geeft `lees(basis)`
   de oorspronkelijke inhoud of een duidelijke fout, en gaat er niets verloren); een client die ná de invoering
   ophaalt en opnieuw opslaat (dat lukt gewoon); en een onderbreking na het journaal, na het verplaatsen,
   na het plaatsen en vóór het afronden (na herstart is er niets verloren en wordt niets als verwijderd gezien). Op
   elk platform dat de desktop-app ondersteunt.

Een grens die blijft: een programma dat een bestand helemaal inleest, het daarna sluit en later zijn oude inhoud
opslaat, werkt vanuit een oudere versie zonder dat de sync dat kan zien. Dat gat bestaat nu ook bij elke gewone
download. De bescherming daartegen is uitchecken en aanwezigheid (§5.7, §5.8), en een programma dat via de werkplek
of de bibliotheek zelf samenvoegt.

**Wat het niet doet.** Geen geometrie begrijpen: een wand die aan beide kanten anders verplaatst is, is een conflict,
ook als de plaatsingen elkaar niet raken. Geen .ifczip en .ifcxml (nog). Geen DWG of PDF: die blijven bij uitchecken
per bestand.

### 5.7 Uitchecken per object

Een gebruiker claimt een lijst GlobalIds in een bestand, met een omschrijving en een vervaltijd (standaard 8 uur,
zoals `SLOT_UREN`). Twee claims op hetzelfde object van verschillende mensen kunnen niet: de tweede krijgt
`object_uitgecheckt` met de objecten en wie ze heeft.

Handhaven gaat in twee treden:

1. **Bij de bron.** Een app of de desktop-sync vraagt `uitgecheckte_objecten` op en zet die objecten op alleen
   lezen, of waarschuwt. Bij het samenvoegen telt een wijziging aan een object dat een ander heeft uitgecheckt als
   conflict.
2. **Op de server**, later. Bij een nieuwe versie berekent de server het verschil met de basis. Raakt dat objecten
   die een ander heeft uitgecheckt, dan wordt de upload een conflictkopie met de reden in het rapport. Dit vraagt de
   bibliotheek op de server (issue voor de kern, §10).

Een app kiest zelf hoe hij een groep objecten maakt: een verdieping met alles erin, een type, een selectie in 3D,
een tak van taken in een planning.

**Welke objecten een wijziging raakt.** De lijst gewijzigde GlobalIds uit het verschil is niet genoeg. Voorbeeld:
alleen de brandwerendheid van wand W wijzigt; het verschil meldt de GlobalId van de eigenschappenset, niet W. Een
wijziging raakt daarom ook elk object waarvoor de gewijzigde definitie geldt, volgens de koppelregel van §5.6 (via
`IfcRelDefines*`, `IfcRelAssociates*` en het type, ook indirect). Ook een gewijzigde, toegevoegde of verwijderde
koppeling telt, bekeken in de basis én in het resultaat. Voorbeeld: alleen de `IfcRelDefinesByProperties` tussen W en
zijn eigenschappenset verdwijnt; W en de set zijn zelf ongewijzigd, maar W verloor zijn eigenschappen en is dus
geraakt (review Sol, hele ontwerp, ronde 2). Eén gedeelde bepaling geldt voor uitchecken (trede 1 en 2) en voor
`anker_status` van opmerkingen (§5.9) (review Sol, hele ontwerp, 5 november).

### 5.8 Aanwezigheid

Een client meldt `meld_aanwezig` bij het openen en daarna elke 30 seconden. Na 90 seconden zonder melding vervalt de
sessie. De server bewaart sessies alleen in Redis, niet in de database: het is vluchtige data. Bij elke wijziging in
de lijst stuurt de server `bestand.aanwezigheid` naar de persoonlijke kamers van wie het bestand op dat moment mag
lezen (dezelfde regel als in §5.4). De desktop-sync kan later ook
aanwezigheid melden voor een bestand dat lokaal open staat (bijv. via de Office-lockbestanden `~$naam` of een slot
van het besturingssysteem).

### 5.9 Opmerkingen

Een draad hoort bij een bestand en een versie (`versie_id`). Het `anker` zegt waar in het bestand:

| Soort bestand | Anker (voorbeeld) |
|---|---|
| IFC-model | `{"soort": "ifc", "objecten": ["2O2Fr$t4X7Zf8NOew3FLOH"], "standpunt": {…}}`; `standpunt` volgt het viewpoint van BCF 3.0 (camera, doorsneden, zichtbaarheid) |
| Planning (IFC van OPS) | `{"soort": "planning", "taken": ["<GlobalId van de taak>"]}` |
| PDF of tekening | `{"soort": "pdf", "pagina": 3, "rechthoek": [x, y, b, h]}` in PDF-eenheden |
| Ander bestand | geen anker: de opmerking gaat over het hele bestand |

`anker_status` bij het opvragen: `bestaat`, `gewijzigd` of `verwijderd` in de huidige versie. Voor IFC en planningen
volgt dat uit het verschil per object. Zo zie je of een opmerking over wand W-12 nog klopt.

@noemen: de server haalt `@<e-mailadres>` uit de tekst. Iemand die het bestand mag lezen, krijgt een melding.
Iemand zonder leesrecht krijgt niets; de API geeft die naam terug in `zonder_toegang`, zodat een scherm later kan
voorstellen het bestand te delen (zoals Word doet). De server deelt nooit vanzelf.

BCF: een draad op een IFC-bestand is een BCF-topic, een reactie een BCF-comment, een anker met standpunt een
BCF-viewpoint. Export en import als `.bcfzip` kan later in dezelfde bibliotheek.

### 5.10 Volgen en meldingen

Wie een bestand bewerkt of er een opmerking op plaatst, volgt het vanzelf (`bron: automatisch`); wie dat niet wil,
ontvolgt. Een volger krijgt bij elke gebeurtenis van een gekozen soort een melding: een Notification Log in de desk,
een push naar zijn eigen kamer, en hoogstens één e-mail per bestand per uur met alles bij elkaar. Een @noemen gaat
altijd meteen. Je krijgt nooit een melding over je eigen handeling. Meldingen respecteren het leesrecht op het moment
van verzenden: na het intrekken van een deling stopt het.

### 5.11 Later: live samen bewerken

Live samen bewerken in dezelfde app (manier C) bouwt op hetzelfde objectmodel: een wijziging is "attribuut X van
object G kreeg waarde W". Het vraagt dat elke app zijn model zo uitdrukt, en een besluit over het transport in de
kern. Het valt buiten deze ronde.

### 5.12 (vervallen)

Hier stond "Opslag via Nextcloud". Nextcloud hoort niet bij de SuperCloud (correctie van Ethan, 5 november 2026). Het
kwam per vergissing uit de eerste opdracht, op basis van de beschrijving van de repo openaec-cloud, zonder na te gaan
of die bij de SuperCloud hoort. De opslag van de SuperCloud is openaec-storage. Het nummer blijft leeg, zodat
verwijzingen naar §5.13 en §5.14 blijven kloppen.

### 5.13 Offline

De desktop-sync werkt offline door. Bij weer online gaat elke upload door de opslaan-lus: samenvoegen waar het kan,
een conflictkopie met rapport waar het niet kan. Een verwijdering uit de offline tijd gaat met haar basis naar de
server (§5.5, "Ook verwijderen is voorwaardelijk"); werkte een ander intussen verder, dan blijft het bestand staan. Uitgecheckte objecten bewaart de client in zijn stand, zodat hij ook
offline kan waarschuwen. Aanwezigheid en meldingen zijn er offline niet.

### 5.14 Rechten en veiligheid

- Alles volgt `vereis_op_plek`: rol in de ruimte of deling, gearchiveerde ruimte alleen lezen, `buiten_deling`.
- Elke nieuwe DocType (opmerkingen, uitchecks, volgers, gebeurtenissen) krijgt dezelfde bescherming als versieregels:
  de controller weigert, niet alleen `has_permission`, want een Frappe-`DocShare` kan een weigering van
  `has_permission` overrulen. Per DocType zijn er negatieve proeven voor lezen, lijst en wijzigen langs de algemene
  routes van Frappe (`/api/resource`, `frappe.client`), ook met een achtergebleven `DocShare` na het intrekken van de
  opslagdeling.
- Realtime: een bericht over een opslagdocument komt alleen in de persoonlijke kamer van een lezer (§5.4, "Geen
  andere route": dicht bij de bron, in de eigen API en bij abonneren). Een negatieve proef per soort kamer (site,
  taak, document, DocType, open formulier) hoort bij stap 6.
- Grens van het vertrouwen: een beheerder van de site (`System Manager`, `Script Manager`) is vertrouwd voor alle
  data van de site; hij kan elke tabel lezen. Dat geldt ook voor elke rol die een sjabloon mag schrijven dat Frappe
  rendert: in de bench `Website Manager`, met ERPNext ook `Accounts User` en `Accounts Manager` (via `Auto Repeat`).
  Alles wat een andere gebruiker kan aanmaken of starten, valt binnen de bescherming (§5.4, "Bij de bron"), ook
  `Auto Email Report` en `Prepared Report`.
- Een deellink zonder account (soort `link`) kan geen opmerkingen plaatsen en ziet geen aanwezigheid: er is geen
  persoon om te tonen of te melden.
- De samenvoegbibliotheek leest onbetrouwbare invoer. Ze mag nooit vastlopen of crashen op een kapot bestand, en ze
  heeft grenzen voor diepte en grootte.
- Een samengevoegde versie is een gewone nieuwe versie. De versie van hun blijft bestaan en is terug te zetten. De
  eigen invoer van ons staat lokaal bewaard (§5.6, "Wat er bewaard blijft"), of als conflictkopie op de server.

### 5.15 De koppeling met de werkplek en de gestreamde desktop

Bron: het contract tussen een app en de SuperCloud in de Blueprint, `docs/supercloud-koppeling.md`
(OpenAEC-Foundation/OpenAEC-Blueprint, `main` op `ba163af9`, 5 november 2026), Infra-issue #75, en het voorbeeld in
Open Calc Studio (`src/services/file/siteHost.ts` en `hostFileActions.ts`, branch `feat/werkplek-app-tools`). Een
tool draait in de SuperCloud op twee manieren: **route A**, als browsertool in de werkplek, met het hostcontract
`window.openaecHost` (`open`, `save`, `list`, `versions`); en **route B**, als desktop-app in een gestreamde desktop,
waar de SuperCloud het bestand uit Docs in een werkmap van de container zet en het teruggezette bestand als nieuwe
versie bewaart. "De SuperCloud doet het transport, de rechten, het slot en de versies."

Wat dat voor dit ontwerp betekent:

- **De opslaan-lus zit voor route A in de host.** Een tool slaat op met `save(bytes, {id, expectedVersion})`, en
  `expectedVersion` is de `version` die `open` gaf. Dat is de basis van §5.5. Een conflict geeft nu een kopie, waarvan
  de tool de naam toont. De tool behandelt `version` als ondoorzichtig (`string | number`; Calc Studio geeft hem
  alleen terug). De host kan daar dus de `versie_id` gebruiken in plaats van het volgnummer, zonder dat er één tool
  verandert. Dat is nodig: een volgnummer kan terugkomen (§5.5). Zie A5.
- **De werkplek voorkomt nu tegelijk bewerken.** `open` geeft `readOnly: true` als "iemand anders het bewerkt". Dat
  is manier A van §5.2 (voorkomen), met een slot per bestand. Voor een soort die de bibliotheek kan samenvoegen
  (IFC) is het voorstel op termijn: geen alleen-lezen voor de tweede, wel aanwezigheid (§5.8), en bij opslaan
  samenvoegen in de host in plaats van een kopie (issue #11). Voor DWG, PDF en andere soorten blijft het slot. Zie
  A12.
- **Route A krijgt eerst alleen conflictkopieën, geen samenvoegen** (review Sol, ronde 7). Het antwoord van `save` is
  `{id, name, version}`; niets zegt dat de host iets anders bewaarde dan de tool stuurde. Calc Studio neemt na `save`
  de nieuwe `version` als basis, en houdt zijn eigen inhoud (`hostFileActions.ts` r. 70–86, commit `4e8ed38`). Na een
  samengevoegde opslag zou de volgende opslag het werk van de ander dus wissen: de basis klopt, de inhoud mist het.
  Opnieuw openen helpt niet altijd: werkte de gebruiker tijdens het opslaan verder, dan valt dat werk weg. Samenvoegen
  in route A komt pas met drie afspraken in het hostcontract:
  1. `save` zegt herkenbaar dat de host samenvoegde (bijv. `merged: true`).
  2. De basis van de tool gaat alleen vooruit samen met de inhoud die hij werkelijk laadt.
  3. De tool bewaart de verstuurde momentopname S tot het antwoord er is. Is het document sinds S niet veranderd,
     dan laadt de tool het resultaat N en neemt hij N als basis. Is het wel veranderd (inhoud L), dan voegt de
     tool samen met basis S, ons L en hun N, en neemt hij daarna N als basis. Dat is dezelfde regel als in §5.6
     voor de desktop-sync (de opslaan-lus, punt 2): N bevat alle wijzigingen van S, dus S is de juiste basis. Bij
     een conflict geeft de tool een conflictkopie, zoals nu. De bibliotheek moet daarvoor in de tool draaien (of
     de host doet die stap met S).

  Een eerdere versie van punt 3 hield de oude basis en de eigen inhoud. Dan verdwijnt een ongedaan maken tijdens
  het opslaan zonder conflict: S zet A van 0 op 1, de gebruiker zet A terug op 0, en samenvoegen met de oude basis
  kiest A = 1 van de server (review Sol, ronde 8).
- **Aanwezigheid hoeft de tool niet te melden.** De host weet wanneer een tool een bestand opent (en of het
  alleen-lezen is) en wanneer het frame sluit; de host meldt zelf `meld_aanwezig` en `meld_weg` namens de gebruiker.
  Naar de tool toe heeft het contract al een gebeurtenis (`openaec-host-preferences`). Het voorstel is een tweede in
  dezelfde vorm, met alleen soort en bestand (zoals de push in §5.4); de inhoud haalt de tool bij de host op
  (issue #10). Zie A8.
- **Route B is een sync.** Het bestand in de werkmap terugzetten als nieuwe versie heeft dezelfde risico's als de
  desktop-sync: zonder de basis gaat werk van een ander verloren (§2), en een programma dat het bestand nog open heeft
  kan in een vervangen bestand schrijven (§5.6, veilig vervangen). Zie A13, en de optie "één sync" in §9.
- **De assistent (MCP).** In route A opent en bewaart de assistent niets zelf ("Openen en opslaan doet de gebruiker
  via het hostcontract"). In route B heeft hij wel `<tool>_open` en `<tool>_opslaan` (Blueprint r. 125–126). Daar
  gelden dezelfde regels als voor route B: de basis, de rechten van de gebruiker en veilig vervangen, ongeacht wie
  het opslaan start (review Sol, ronde 7).

## 6. Keuzes

Alle keuzes: genomen door Claude (Opus 5.5) binnen de opdracht van Ethan van 5 november 2026, nog niet beoordeeld.

| # | Keuze | Reden | Alternatief dat het niet werd |
|---|---|---|---|
| K1 | Samenvoegen op GlobalId en inhoud, niet op STEP-nummer | OPS en andere programma's nummeren bij elk opslaan opnieuw; de GlobalId blijft (IFC eist dat) | ifcmerge-aanpak op `#nummers`: werkt alleen met programma's die de nummers laten staan (Bonsai) |
| K2 | Eigen STEP-lezer in Rust, geen IfcOpenShell | Moet in de desktop-app en later in de browser (WebAssembly) draaien, zonder C++-afhankelijkheid; we hebben alleen de graaf nodig, geen geometrie | IfcOpenShell (Python/C++): zwaar om mee te leveren in Tauri en in de browser |
| K3 | Samenvoegen eerst in de client, later ook op de server (herzien op 5 november zonder Nextcloud; de keuze blijft) | Werkt offline; de server hoeft alleen vergelijk-en-schrijf te kennen; de bibliotheek hangt niet af van de kern, die nog niet gelezen is | Alleen op de server: werkt niet offline, en vraagt eerst een besluit over de kern (issue #13, A6 in §9) |
| K4 | Bij een conflict standaard geen automatische keuze, maar een conflictkopie met rapport | Een verkeerde automatische keuze in een model kost meer dan een kopie; zo gaat er nooit iets verloren | "Laatste wint" per attribuut: sneller, maar stil verlies |
| K5 | De bibliotheek staat in openaec-cloud-desktop (`crates/openaec-samenvoegen`) | Daar zitten de Rust-code, de sync en de conflicten al; los van `openaec-sync`, dus ook bruikbaar voor de server en de apps | In openaec-storage naast `gateway/`: dichter bij de server, maar de eerste gebruiker is de desktop-app |
| K6 | Eigen `Opslagopmerking` in plaats van Frappe `Comment` | We hebben anker, versie, draad, status en BCF nodig; `Comment` heeft dat niet | Frappe `Comment`: bestaat al met @noemen in de desk, maar zonder plek in het bestand |
| K7 | Lezers mogen opmerkingen plaatsen | In de bouw beoordelen opdrachtgever en adviseur tekeningen en modellen die ze niet mogen wijzigen | Alleen schrijvers: M365 kent daarvoor een aparte rol "kan beoordelen" |
| K8 | Aanwezigheid via HTTP-meldingen met een levensduur, opgeslagen in Redis | Werkt voor elke client (desktop, apps, browser), ook zonder socket.io; geeft ook modus en app | Frappe `doc_viewers` via socket.io: alleen voor de desk in de browser |
| K9 | Uitchecken per object eerst bij de bron gehandhaafd, op de server pas met de bibliotheek | Handhaven op de server vraagt het verschil bij elke upload; dat kan pas als de server de bibliotheek draait | Meteen op de server: blokkeert op een besluit over de kern |
| K10 | Eén gebeurtenisfunctie voor wijzigingenlijst, push, haak en meldingen | Elke weg (browser, sync, WebDAV, batch, uploadlink) komt langs dezelfde plek; niets wordt vergeten | Per functie apart: zo ontstaan nu al gaten (geen haak, geen push) |
| K11 | `bij_conflict="weiger"` als optie, standaard blijft de conflictkopie | Bestaande clients (desktop, WebDAV, browser) veranderen niet | Het gedrag van `upload_klaar` voor iedereen veranderen: breekt oudere desktop-apps |
| K13 | Eerst vergelijk-en-schrijf op de server (`bij_conflict=weiger`), dan pas samenvoegen in de desktop-sync | Zonder atomair vergelijk-en-schrijf moet de client een conflictkopie achteraf opruimen, en dat kan niet veilig (review Sol, 5 november) | Een tussenweg met de bestaande API: samenvoegen vóór het uploaden en de conflictkopie daarna in de prullenbak |
| K14 | Verwijzingen naar knopen van een ander object tellen als anker, niet als inhoud | Anders gaat een herplaatsing (wand onder een andere verdieping) verloren als de plaatsingen gelijk zijn (review Sol) | Alleen inhoudshashes: eenvoudiger, maar verliest data |
| K15 | De regel in de wijzigingenlijst in dezelfde transactie als de handeling; push, haak en meldingen erna | Een versie zonder regel mist de sync en de haak (review Sol) | Alles na de commit: dan kan een uitval een regel kosten |
| K16 | Een commitnummer, uitgedeeld bij het lezen: een veger nummert vastgelegde regels met nummer 0 onder een kort slot op één tellerregel, en commit vóór het lezen | Volgnummers komen in invoegvolgorde, niet in zichtbaarheidsvolgorde, en een client kan regels missen; de veger geeft een laat zichtbare regel altijd een hoger nummer dan elke uitgedeelde cursor, langs welke commitroute de regel ook kwam (review Sol, ontwerp ronde 2 en 3; PR #16 ronde 1 en 2) | Nummeren vlak voor de commit (`before_commit`): elke commitroute buiten die haak laat een regel zonder nummer; een cursor die vóór een jong gat stopt: veilig alleen bij een tijdgrens die een taak in `long` (25 minuten) niet haalt |
| K17 | Gedeelde ankers krijgen de verzameling van hun ankers als identiteit; twijfel is een conflict | Een gekozen vertegenwoordiger verspringt als een programma een plaatsing splitst (review Sol) | Een vaste keuze, bijv. het kleinste anker |
| K18 | Na samenvoegen de boom controleren: geen kring, niets wat bereikbaar was raakt los | Twee geldige herindelingen kunnen samen een kring maken, terwijl elke cardinaliteit klopt (review Sol, ronde 3) | Alleen cardinaliteiten uit het schema controleren |
| K19 | De basis is het id van de versieregel (`versie_id`), niet het versienummer | Een nummer kon terugkomen of dubbel worden uitgegeven, ook vóór de invoering, op manieren die achteraf niet vast te stellen zijn (review Sol, rondes 10 tot en met 13); het id komt nooit terug en de inhoud van een regel verandert nooit | Een nummer dat nooit terugkomt, met een patch die de huidige versie hernummert boven een berekende bovengrens: elke bovengrens bleek een gat te hebben |
| K12 | Live samen bewerken (C) pas later | Vraagt dat elke app zijn model als wijzigingen per object uitdrukt, en een besluit over het transport | Nu al een sessiedienst: zonder app die hem gebruikt, is hij niet te testen |

## 7. Volgorde van bouwen

Elke stap is een eigen draft-PR die op zichzelf werkt, met een oordeel van Sol (GPT-6.1-Sol).

| Stap | Wat | Repo | Hangt af van de kern? |
|---|---|---|---|
| 1 | Dit ontwerp | openaec-storage | Nee |
| 2 | Bibliotheek: IFC lezen, objectmodel, verschil | openaec-cloud-desktop | Nee |
| 3 | Bibliotheek: samenvoegen, schrijven, opslaan-lus, met een proef van 8 clients tegelijk in het geheugen | openaec-cloud-desktop | Nee |
| 4 | Storage: een nieuwe versie vastleggen onder een slot op het bestand of de map (§2), vergelijken op bestand én versie (`basis_bestand`), de basis als id van de versieregel (`basis_versie_id`, `huidige_versie_id`, `download(…, versie_id)`, `onbekende_basis`), versieregels niet te wijzigen via `/api/resource`, een slot op het doel bij verplaatsen, een patch zonder hernummeren, `bij_conflict=weiger`, `samengevoegd_uit` | openaec-storage | Nee, maar de owner beoordeelt de API |
| 4b | Storage: een commitnummer voor de wijzigingenlijst, zodat de cursor geen regels overslaat (§5.4) | openaec-storage | Nee |
| 5 | Desktop-sync: IFC samenvoegen via de opslaan-lus, en de proef met twee clients tegen een echte openaec-storage-server (met stap 4), als script met log | openaec-cloud-desktop | Nee |
| 6 | Storage: gebeurtenissen (push en haak, issue #7), `zet_terug`, `activiteit` | openaec-storage | Deels: push vraagt socket.io in de kern |
| 7 | Aanwezigheid | openaec-storage of kern | Ja |
| 8 | Opmerkingen met @noemen en anker | openaec-storage of kern | Ja |
| 9 | Volgen en meldingen | openaec-storage of kern | Ja |
| 10 | Uitchecken per object | openaec-storage | Deels |
| 11 | `verschil` en handhaven op de server | openaec-storage | Ja: hulpprogramma naast Frappe |
| 12 | Live samen bewerken | kern en apps | Ja |

Stap 2 tot en met 5 (met 4b) bouw ik nu. Stap 6 en verder wachten tot de kern gelezen is.

Schaal (§12) staat niet als eigen stap in de tabel. In de bibliotheek komt nu een weg per object (§12.1, G1 tot en met
G3, branch `samenvoegen/gb-schaal`). Bij stap 4b hoort de veger in delen (§12.2, D3). Bij stap 10 hoort, als die keuze
valt, één regel per uitgecheckt object met een unieke index (D5). De rest van §12 wacht op de kern.

## 8. Welke schermen er later op kunnen

Niets hiervan wordt nu gebouwd. Dit is wat de onderlaag mogelijk maakt:

- In `/opslag`, de werkplek en de desktop-app: gezichten van wie het bestand open heeft, met "bewerkt" of "bekijkt".
- Een paneel met opmerkingen naast het model, de tekening of de planning, met BCF-standpunten; klikken springt naar
  het object.
- "Wat is er veranderd" tussen twee versies: een lijst per object, en in de 3D-viewer kleuren voor toegevoegd,
  gewijzigd en verwijderd. Voor een planning: verschoven taken in de Gantt.
- Een balk na het opslaan: "Samengevoegd met de wijzigingen van Piet (3 taken)".
- Een conflictscherm: per object en attribuut kiezen tussen ons en hun.
- In de 3D-viewer: objecten selecteren en uitchecken; uitgecheckte objecten in de kleur van de persoon.
- Een bel met meldingen, en een dagelijkse of uurlijkse e-mail.
- In de Verkenner (desktop): een overlay "in gebruik door Anna".

## 9. Open aannames over de kern

De kern (`openaec-supercloud`, org OpenAECStudio) is niet gelezen. Besluit van Ethan (5 november 2026): op de server
wordt niet verder gebouwd tot de kern gelezen is. Storage-PR #9 en #16 blijven draft. Dit hoofdstuk zegt daarom per
aanname drie dingen: de kleinste vorm van de aanname, welke keuzes in dit ontwerp ervan afhangen, en wat ik in de kern
als eerste lees. Keuzes die lang moeten meegaan, staan hier als opties. Ze worden pas vastgelegd als de kern gelezen
is. Sinds 5 november is er één openbare bron over de kern: het contract tussen een app en de SuperCloud
(§5.15). Wat daaruit volgt, staat bij A5, A8, A12 en A13.

| # | Aanname, zo klein mogelijk | Keuzes die ervan afhangen | Eerst lezen in de kern |
|---|---|---|---|
| A1 | De inhoud en de versies van een bestand in een ruimte staan alleen in openaec-storage (`Bestandsversie` + S3), en niets schrijft er buiten de API van openaec-storage om | §5.3, §5.4 (de wijzigingenlijst als enige bron), stap 4 en 4b, K3 | Welke app de DocTypes voor bestanden en versies bezit; of de kern zelf naar S3 of naar `tabBestandsversie` schrijft (zoek in de code op `Bestandsversie`, `s3`, `boto3`) |
| A2 | Een ingelogde client kan één realtime-kanaal per site volgen, de realtimeserver vraagt bij abonneren `frappe.realtime.has_permission`, en de Frappe van de kern kent dezelfde soorten configuratie die op een document reageert als de bench (§5.4) | §5.4 (push, geen gedeelde kamers), §5.8, §5.10, §5.14 | De Procfile of compose van de kern (draait `socketio`, en welke versie: de Node-server of die in Python?), welke controle die bij abonneren vraagt, de Frappe-versie en welke DocTypes in de kern op `run_method` van een document reageren (`Notification`, `Webhook`, `Automation Flow` …), de proxy-instelling voor `/socket.io`, en welk soort token de desktop-app krijgt |
| A3 | De kern heeft nog geen eigen DocTypes voor opmerkingen, meldingen of activiteit op bestanden | §5.9, §5.10, K6 | De lijst van DocTypes in de kern (alle `doctype/*/*.json`) en waar `Notification Log` wordt gebruikt |
| A4 | Elke toegang tot een bestand loopt via `rechten.vereis_op_plek` (rol in de ruimte of deling), en alleen beheerders van de site hebben `System Manager` of `Script Manager` | §5.14, alle rechten in §5.5, de bescherming van versieregels (stap 4), de grens van het vertrouwen in §5.4 | De `has_permission`- en `permission_query_conditions`-haken van de kern, hoe openaec_beheer gasten en groepen maakt, of de kern `DocShare` gebruikt, en wie in de kern `System Manager` of `Script Manager` krijgt. Ook: welke apps in de kern draaien (ERPNext?), welke rollen daar een sjabloon mogen schrijven (`Website Manager`, `Accounts User` …), en wie die rollen krijgt (§5.4, review Sol ronde 6). En wie `Report Manager` of `Prepared Report User` heeft, en of rapporten van de kern in de achtergrond worden voorbereid (§5.4, review Sol ronde 8) |
| A5 | De conflictregel in het ontwerp van openaec-storage in de kern (`docs/superpowers/specs/2026-10-20-openaec-storage-design.md` §6) is dezelfde als in de code: een upload wint alleen bij de juiste basis. Het hostcontract bevestigt de vorm: `save(bytes, {id, expectedVersion})`, en een conflict geeft een kopie (§5.15). Open is nog: is `version` daar het volgnummer of een id dat nooit terugkomt? | §5.5 (de basis), `bij_conflict`, §5.15 | Dat document, §6; en in `openaec_werkplek` wat de host als `version` teruggeeft en hoe hij `expectedVersion` aan storage doorgeeft |
| A6 | Naast Frappe kan een los programma draaien (zoals de WebDAV-gateway van openaec-storage in een eigen container) | Handhaving op de server (§5.7 trede 2), `gewijzigd` bij opmerkingen (§5.9), samenvoegen op de server | De compose- of deployconfig van de kern: welke containers er zijn, en of er een plek is voor een hulpprogramma |
| A7 | De `BIM Issue` van open-bim-platform en opmerkingen op bestanden mogen één systeem worden | §5.9, K6 | Of de kern al naar `BIM Issue` verwijst, en wie de eigenaar van dat DocType is |
| A8 | De host kan een tool een gebeurtenis over een bestand sturen, zoals hij nu al `openaec-host-preferences` stuurt (§5.15), en de host hoort zelf nieuwe versies (via A2 of de wijzigingenlijst) | Aanwezigheid en nieuwe versies in de browser-apps (#10) | In `openaec_werkplek`: hoe `openaec-host-preferences` wordt verstuurd, en of de host berichten van de server ontvangt |
| A12 | Het slot dat de werkplek gebruikt voor `readOnly` ("iemand anders bewerkt het") is het slot per bestand van openaec-storage (`vergrendel`, `Opslagvergrendeling`) | §5.2 manier A, §5.7, samenvoegen in de werkplek (#11), de vergelijking met Microsoft 365 (§4a) | Waar `openaec_werkplek` `readOnly` bepaalt, en hoe lang zo'n slot blijft staan |
| A13 | Route B (de gestreamde desktop) zet een bestand terug met de versie die hij in de werkmap zette als basis | §5.6 (de opslaan-lus), §5.15 | De code die de werkmap van de container vult en terugzet: geeft die een basis mee, en vervangt hij een bestand dat een programma nog open heeft? |
| A9 | De database is MariaDB 11.8 of nieuwer, met REPEATABLE READ en `innodb_snapshot_isolation` aan | §5.4 (de veger), de transacties van stap 4 | De image en de `my.cnf` van de database in de deploy van de kern. Het team gebruikt nu een image met erpnext v16.51.1; de proeven draaiden op Frappe 16.55.2 |
| A10 | Er draaien achtergrondwerkers (RQ) en de scheduler | Meldingen bundelen (§5.10), de haak (§5.4), een taak voor `anker_status` | De Procfile of compose: `worker`, `schedule` |
| A11 | De opslag in productie gebruikt dezelfde blokken van 8 MiB met BLAKE3 als de code van openaec-storage. Die blokken liggen op een vaste plek; na samenvoegen gaan dan alle blokken opnieuw (meting 07, desktop-PR #12). Voor een kleine upload is hergebruik op inhoud nodig | Alleen de gewijzigde blokken versturen bij grote modellen (§5.6, hoofdstuk 12) | De opslaginstellingen in de deploy van de kern |

**Volgorde van lezen** zodra er toegang is: eerst A1 en A4 (wie bezit de bestanden en de rechten), dan A9 en A10 (de
database en de werkers), dan A2, A6, A8, A12 en A13 (realtime, hulpprogramma's, werkplek), dan A3, A5, A7 en A11. Daarna werk ik
dit ontwerp bij, en pas dan wordt er weer op de server gebouwd.

**Keuzes die lang meegaan, als opties tot de kern gelezen is:**

- **Waar de samenwerkdiensten wonen** (opmerkingen, aanwezigheid, volgen, meldingen, uitchecken per object).
  Optie 1: in openaec-storage, naast de bestanden (nu het voorstel; rechten en bestanden op één plek). Optie 2: in de
  kern, als die al zulke diensten heeft (A3). Optie 3: een eigen app tussen beide. Beslissend: A1, A3 en A4.
- **Het transport voor push.** Optie 1: socket.io van Frappe. Optie 2: een eigen stroom (SSE) per site. Optie 3: alleen
  vragen (`wijzigingen` met een cursor). Optie 3 werkt altijd en blijft de bodem: wie niets mag missen, leest de lijst.
  Beslissend: A2.
- **Samenvoegen op de server** (voor de browser en WebDAV, en als wachtrij bij veel schrijvers). Optie 1: het
  Rust-hulpprogramma naast Frappe. Optie 2: de bibliotheek als Python-extensie. Optie 3: alleen in de client.
  Beslissend: A6.
- **De vorm van de basis in de API** (`basis_versie_id` uit PR #9). De bibliotheek en de sync zien alleen de trait
  `Opslag`; de vorm in de API kan dus nog veranderen zonder dat de bibliotheek verandert. Beslissend: A1 en A5.
- **Eén systeem voor opmerkingen** (eigen DocType of `BIM Issue`). Beslissend: A3 en A7.
- **Slot of samenvoegen in de werkplek, per soort bestand.** Optie 1: het slot blijft voor alles (nu; de tweede
  persoon krijgt alleen-lezen). Optie 2: geen slot voor een soort die de bibliotheek kan samenvoegen (IFC), wel
  aanwezigheid en samenvoegen bij opslaan; het slot blijft voor de rest. Optie 2 is het voorstel: alleen zo werken
  twee mensen tegelijk aan één model. Het vraagt wel eerst de drie afspraken over `save` uit §5.15; tot dan blijft
  optie 1. Beslissend: A12, en waar het samenvoegen draait (A6).
- **Eén sync voor de desktop en route B.** Optie 1: de gestreamde desktop gebruikt dezelfde sync als de desktop-app
  (`openaec-sync`: de basis, veilig vervangen, samenvoegen bij opslaan). Optie 2: de kern houdt een eigen werkmap,
  die dan dezelfde regels moet volgen. Optie 1 is het voorstel: één plek voor dezelfde risico's. Beslissend: A13.
- **Automatische en voorbereide rapporten.** Optie 1: `Auto Email Report` blijft uit en `Prepared Report` is alleen
  voor beheerders (de eerste versie, §5.4). Optie 2: een `Auto Email Report` mag weer aan, als een `Report` dat een
  aanstaand automatisch rapport gebruikt, alleen door een `System Manager` te wijzigen of te verwijderen is; en een
  `Prepared Report` is te lezen door wie het aanvroeg (ook in de lijst en voor de bijlage, zonder delingen). Optie 2
  vraagt eerst een eigen review. Beslissend: A4 (wie heeft `Report Manager` en `Prepared Report User`, en leunt de
  kern op voorbereide rapporten van ERPNext).
- **Een eigen site voor de opslag.** Optie 1: de opslag deelt de site met de andere apps (nu). Dan is elke rol die
  een sjabloon mag schrijven, vertrouwd voor alle gegevens van de opslag, ook de bestanden (§5.4). Optie 2: de opslag krijgt een eigen
  site met een eigen database; alleen de beheerders van die site zijn dan vertrouwd. Dat kost wel een aanmelding over
  twee sites heen. Optie 1 is het voorstel zolang alleen beheerders zulke rollen krijgen. Beslissend: A4.

## 10. Issues voor de owner van de kern

Deze staan als issues in openaec-storage, zoals #2 tot en met #7:

1. #10 Werkplek: aanwezigheid en gebeurtenissen van een bestand aan een tool doorgeven.
2. #11 Werkplek: samenvoegen bij opslaan vanuit een tool, in plaats van een conflictkopie.

   Bij #10 en #11 staat een reactie die het voorstel op het hostcontract van de Blueprint legt (§5.15): de host meldt
   aanwezigheid zelf, en een gebeurtenis in de vorm van `openaec-host-preferences`. Samenvoegen in `save` vraagt wel
   een nieuwe vorm voor de tool: een herkenbaar antwoord en een basis die bij de geladen inhoud hoort (§5.15, review
   Sol ronde 7). Tot dan geeft route A conflictkopieën.
3. #12 Realtime in de SuperCloud: draait socket.io, en kan de desktop-app er met een token bij?
4. #13 Server: een Rust-hulpprogramma naast Frappe voor verschil en samenvoegen van IFC.
5. #14 Eén systeem voor opmerkingen op bestanden en BIM-issues (BCF).
6. #15 Meldingen: één plek voor meldingen en e-mailbundels in de SuperCloud.

## 11. Bronnen

- openaec-storage, `main` op commit `e87b870`: `README.md`, `openaec_storage/api.py`, `rechten.py`, `delen.py`,
  `dossier.py`, `hooks.py`.
- openaec-cloud-desktop, `main`: `crates/openaec-sync/src/motor.rs`, `opslag.rs`, `api.rs`, `src-tauri/src/bewaking.rs`,
  `src-tauri/src/sloten.rs`.
- open-planner-studio: `src/services/ifc/ifcWriter.ts` (GlobalIds en nummering), `examples/*.ifc`, en het ontwerp
  in draft-PR #797 (`docs/relaties-tussen-projecten.md`, branch `docs/relaties-tussen-projecten-supercloud`).
- open-bim-platform: `docs/superpowers/specs/2026-10-26-open-bim-platform-design.md` (§5.6 BIM Issue en BCF, §6.2
  ifcmerge, §7.4 eisen aan storage).
- Microsoft 365 en AEC-tools: de links in §3.
- OpenAEC-Blueprint, `main` op `ba163af9`: `docs/supercloud-koppeling.md` (het contract tussen een app en de
  SuperCloud). Open-Infra-Studio issue #75. Open Calc Studio, branch `feat/werkplek-app-tools`:
  `src/services/file/siteHost.ts`, `src/services/file/hostFileActions.ts`. Het voorbeeld van Open PDF Studio
  (`js/core/werkplek-app.js`) staat niet op de standaardbranch van `open-pdf-studio`; volgens het contract gaat het
  over de assistent, niet over bestanden.

## 12. Schaal en duurzaamheid

De richting van Ethan (5 november 2026): "zorg dat hij flexibel en duurzaam is voor de zeer lange termijn en met grote
hoeveelheden deelnemers. uiteindelijk willen we natuurlijk ook samenwerken in BIM modellen van meerdere gigabytes. je
hoeft niet hetzelfde te doen als MS, als het maar beter of net zo goed is."

Dit hoofdstuk geeft per onderwerp het probleem met een getal, de opties, de voorkeur met de reden, de kosten en het
bewijs. Op de server wordt niets gebouwd tot de kern gelezen is (§9). Keuzes voor de server staan hier daarom als
opties, met de aanname die beslist. Gebouwd is alleen wat er "gebouwd" bij staat. Een getal met "schatting" is gerekend,
niet gemeten.

Doelen (voorstel van Claude, nog niet beoordeeld door Ethan):

| Doel | Getal |
|---|---|
| Grootte van één model | 5 GB IFC zonder harde grens in bibliotheek of opslag |
| Geheugen bij verschil en samenvoegen | hoogstens 2 GB, ook bij 5 GB |
| Versturen na een kleine wijziging | ongeveer de grootte van de wijziging, niet van het bestand |
| Deelnemers | 300 tegelijk actief in één ruimte, 1 000 leden op één site |
| Levensduur | een versie van nu is over tientallen jaren nog te lezen en te vergelijken, ook zonder onze software |

### 12.1 Modellen van meerdere gigabytes

**Het probleem.** De bibliotheek leest de bestanden helemaal in, en houdt per versie een model in het geheugen.
Samenvoegen heeft er vier: basis, ons, hun, en het resultaat voor de zelfcontrole. Metingen van P4
(`docs/samenwerken/metingen.md` op `samenvoegen/p4-metingen` in openaec-cloud-desktop, commit `daa7748`, bibliotheek
`0655eda`, elke run onder een harde grens van 2 GB):

| Model | Grootte | `voeg-samen` | Piekgeheugen |
|---|---|---|---|
| Dicht (43 bytes per instantie, zoals een gewone BIM-export) | 92 MB | 6,4 s | 1,6 GB (17 MB per MB) |
| Veel geometrie in lange regels (1 900 bytes per instantie) | 150 MB | 5,4 s | 670 MB (4,4 MB per MB) |
| Dicht | 1,2 GB | — | gestopt door de grens: al tijdens het lezen van één bestand (`tel`, na 7,5 s) |
| Dicht | 6,3 GB | — | gestopt tijdens het inlezen; zonder grens zou `Fout::TeGroot` volgen (4 GiB of groter, `ontleden.rs` r. 157) |

De invoer is een open model van buildingSMART (CC BY 4.0), vermenigvuldigd met nieuwe nummers en GlobalIds. Het
geheugen volgt de formule 3,9 × de bytes + 670 bytes per knoop + 2,8 kB per object (een fit over 35 metingen tot
174 MB). Doorgetrokken (schatting van P4) vraagt een dicht model van 1 GB 18,5 GB om samen te voegen, en een van 5 GB
92 GB. Of echte modellen van die grootte hetzelfde doen, is niet gemeten; dat de opzet van nu niet tot meerdere GB
gaat, wel (de regels van 1,2 en 6,3 GB hierboven). De
tijd is het probleem niet: ongeveer 4 s per 100 MB, waarvan een derde de zelfcontrole. Ook het contract is op hele
bestanden gebouwd: `Opslag::lees` geeft een `Vec<u8>` en `schrijf_als` neemt een `&[u8]` (`samenwerken.rs` r. 53-67).

Versturen schaalt ook slecht. De server hergebruikt een blok van 8 MiB alleen als **dezelfde plek** in de basis dezelfde
hash heeft (`api.py` `_herbruikbare_blokken`, r. 308). Wordt één regel een byte langer, dan schuift alles erachter.
Voorbeeld: in een model van 1 GiB (128 blokken) verandert één wand in het midden. Dan gaan 64 blokken (512 MiB) opnieuw
over de lijn. Downloaden met `blokken` werkt op dezelfde manier.

**G1. Een objectindex in plaats van het hele model.** Het bestand blijft waar het is (mmap). Per instantie houdt de
bibliotheek 5 bytes bij: de plek in het bestand en 7 bits (object, wie ernaar verwijst, anker, vluchtig type). Per
object zijn het 64 bytes: het nummer, een sleutel uit de GlobalId en de inhoudshash volgens de regels van §5.6.
Attributen leest ze opnieuw uit de tekst als ze nodig zijn. Een inhoudshash hangt af van de knopen waar een knoop naar
wijst, en die staan soms verderop in het bestand. Daarom rekent ze in een paar doorlopen eerst uit wat globaal is
(kringen, ankers, gedeelde knopen), en dan per object de hash (`docs/samenwerken/gb-schaal.md` in
openaec-cloud-desktop). Rekensom (schatting) voor het testmodel van 6,3 GB van P4 (116 miljoen instanties, 780 000
objecten): 0,6 GB per versie. `verschil` heeft er twee, samenvoegen drie: ongeveer 1,9 GB, net onder de grens van
2 GB. Een bestand met nummers die niet oplopen, valt terug op ongeveer 40 bytes per instantie; dat haalt de grens niet.
- De client bewaart de index van de basis naast de momentopname S in `.openaec/` (§5.6). Samenvoegen hoeft dan alleen
  ons en hun te indexeren.
- De index is een cache: hij is altijd opnieuw te maken uit het IFC-bestand (zie §12.3).
- Elke client rekent de index zelf uit. Een index van een andere client kan fout zijn, door een fout of met opzet, en
  dan verdwijnt een wijziging stil. Een index van de server is alleen te vertrouwen als de server de bibliotheek draait
  (A6).
- Status: `verschil_stromend` wordt gebouwd op `samenvoegen/gb-schaal` (openaec-cloud-desktop, PR #20), met dezelfde
  uitkomst als `verschil` op elke test. Samenvoegen is daar nog een plan (5 november, 17:10).

**G2. Alleen gewijzigde objecten lezen en samenvoegen.** Gelijke hash is gelijk. Alleen een object met een andere hash
leest de bibliotheek uit het bestand, met zijn knopen, en daarop gelden de regels van §5.6. De schrijver kopieert de
bytes van ons en zet alleen de vervangen en nieuwe regels erin. De controles na het samenvoegen (de koppelregel, de
boomregel, kringen, de grenzen van INVERSE's) hebben de relaties nodig, niet de geometrie. De index houdt daarom de
graaf van objecten en relaties. De zelfcontrole leest het resultaat streamend opnieuw en vergelijkt de hashes per
object.
- De zelfcontrole kost nu een derde van de tijd en het vierde model (P4, profiel per fase). Een smallere controle
  (alleen wat het samenvoegen raakt, plus de tellingen) mag pas als een test laat zien dat ze dezelfde fouten vindt:
  de zelfcontrole is het vangnet tegen een fout in het schrijven.
- Bewijs: een test op het hele testcorpus (dezelfde gewijzigde objecten en dezelfde conflicten als de weg van nu), en
  metingen op 1 en 5 GB onder een harde grens van 2 GB (`systemd-run --user --scope -p MemoryMax=2G`). Beide op
  `samenvoegen/gb-schaal`.

**G3. Het contract van de opslag.** Opties: (a) hele bestanden in het geheugen, zoals nu; (b) paden:
`lees(versie, naar: &Path)` en `schrijf_als(basis, van: &Path)`; (c) `Read`- en `Write`-stromen. Voorkeur (b): de sync
heeft de bestanden al op schijf, en de index wil mmap. Kosten: de trait en zijn twee implementaties (de proefopslag in
de tests en de sync). Samen met G2, want pas dan heeft het effect.

**G4. Alleen wijzigingen versturen.**

| Optie | Wat | Na één lokale wijziging in 5 GB | Na 1 000 verspreide wijzigingen in 5 GB | Hangt af van |
|---|---|---|---|---|
| V1 (nu) | Vaste blokken van 8 MiB; hergebruik alleen op dezelfde plek in de basis | gemiddeld de helft van het bestand | bijna alles | — |
| V2 | Blokgrenzen bepaald door de inhoud (content-defined chunking), 5 tot 16 MiB, een grens alleen na het einde van een instantie; hergebruik op hash uit de basis en de eerdere versies van hetzelfde bestand | één tot enkele blokken (8 tot 50 MiB) | ongeveer 80 % van de 600 blokken (schatting) | A11; servercode ligt stil |
| V3 | Een objectdelta: alleen de gewijzigde objecten als STEP-regels en de verwijderde GlobalIds; de server bouwt de versie met de bibliotheek | ongeveer de wijziging (kB tot MB) | idem | A6 |

- Waarom minstens 5 MiB: in S3 moet elk deel van een multipart-upload minstens 5 MiB zijn (behalve het laatste), en de
  server kopieert een stuk van de basis per deel (UploadPartCopy). Zo blijft elke versie één gewoon IFC-object in S3,
  zoals nu. Kleinere blokken vragen een eigen S3-object per blok en een lijst per versie. Dan is een versie in S3 geen
  leesbaar IFC-bestand meer, en dat botst met §12.3.
- Blokken op objectgrenzen helpen alleen samen met grenzen die de inhoud bepaalt. Vaste blokken die op een objectgrens
  knippen, verschuiven nog steeds. Met V2 zorgen ze dat een gewijzigde instantie in één blok valt.
- Hergebruik alleen binnen hetzelfde bestand. Hergebruik over bestanden heen laat een gebruiker met een hash testen of
  een ander dezelfde inhoud heeft.
- V2 werkt voor elke bestandssoort, ook DWG en PDF. V3 werkt alleen voor IFC, en de server moet byte voor byte hetzelfde
  bestand maken als de client.
- Voorkeur: V2 als volgende stap zodra de server weer mag. V3 alleen als metingen tonen dat wijzigingen meestal
  verspreid zijn, en als A6 klopt.
- Kosten van V2: het knippen in de client (`blokken.rs` in openaec-sync), en hergebruik op hash in `upload_start` en
  `blokken` op de server; de opslag in S3 blijft gelijk. Kosten van V3: de bibliotheek als hulpprogramma op de server
  (issue #13).
- Bewijs (niet gebouwd): een meting zonder server die voor echte paren van versies telt hoeveel bytes er onder V1, V2 en
  V3 over de lijn gaan. Paren: de voorbeelden van OPS, de invoer van P4 (`weinig`, `groot`), en versies uit een
  ontwerpprogramma. Het hoort naast `crates/openaec-samenvoegen/scripts/meting/` van P4.

**G5. Een model deels laden.** Opties: (a) federatie: het projectmodel bestaat uit meer IFC-bestanden, bijv. per
discipline of per bouwdeel, zoals in BIM gebruikelijk is (eigen kennis); (b) deels laden met de objectindex: een app
vraagt "verdieping 3 met alles erin", de index geeft de objecten, hun knopen en de plekken in het bestand, en de client
leest alleen die stukken. Voorkeur (a): het werkt nu, met elk programma, en het verlaagt ook het aantal schrijvers per
bestand (D6 in §12.2). Het kost geen software, alleen een afspraak in het project. (b) bouwt later op G1, als een app
erom vraagt. Terugschrijven vanuit een deel vraagt dan de hele basis in de client, of V3 op de server.

### 12.2 Honderden deelnemers aan één project

Rekenvoorbeeld: 300 actieve deelnemers in één ruimte met 20 000 bestanden, elk met de desktop-app. De app vraagt elke
30 s `wijzigingen`, en doet elke 10 minuten hoe dan ook een ronde (`bewaking.rs` r. 19-21, main `a3aceac`).

**D1. Elke ronde vraagt de hele boom.** In de desktop-sync is de wijzigingenlijst alleen het sein dat er iets nieuws
is. Daarna haalt elke ronde de hele ruimte op met `boom` (`motor.rs` `sync_met_opties` en `opslag.rs`
`lijst_als_boom`, main `a3aceac`). De simulatie (P2) laat het zien: 191 rondes en 191 keer `boom` (run `b-zaad1`,
8 clients, desktop `1b451e1`, server `197d55b`). Rekenvoorbeeld: 300 deelnemers slaan samen ongeveer één keer per
seconde op. Dan ziet elke client bij elke controle iets nieuws en vraagt hij 20 000 regels. Samen is dat
300 × 20 000 / 30 s = 200 000 regels per seconde voor één ruimte (schatting). De 10 HTTP-verzoeken per seconde naar
`wijzigingen` zijn daarnaast klein: met 4 werkers van gunicorn verwerkte de proefbench ongeveer 250 verzoeken per
seconde (P2, lastproef `d-last-4werkers`, ruw, zie D6).
- Opties: (a) zoals nu; (b) een ronde op de wijzigingen: de client past de regels uit de lijst toe op zijn beeld van de
  server, en vraagt alleen de bestanden en mappen die daarin staan; de hele boom alleen bij het starten, na
  `cursor_te_oud` (D2) en één keer per dag; (c) `boom` met een `sinds`, zodat de server alleen gewijzigde regels geeft.
- Voorkeur (b): de regels staan al op de server. De hele boom als vangnet mag pas zelden als de cursor niets meer
  overslaat, dus alleen tegen een server met 4b (§5.4, `mogelijkheden()`). Een verplaatste map verandert het pad van
  alles eronder; de client houdt mappen daarom bij hun id bij, niet bij hun pad. (c) is minder werk in de client, maar
  vraagt servercode.
- Kosten: een tweede weg in de motor van de sync, naast de volledige ronde.
- Bewijs: in de simulatie volgt op elke ronde op de wijzigingen een volledige ronde, en beide geven hetzelfde beeld. P2
  telt al de gemiste regels per client: 0 van de 299 in run `b-zaad1`.

**D2. De wijzigingenlijst blijft groeien.** Schatting: 300 deelnemers met elk 100 regels per dag (een app die elke 5
minuten opslaat) geven 11 miljoen regels per jaar in één ruimte, ongeveer 2 GB. De index op `(ruimte, commitnummer)`
(PR #16) houdt het lezen snel. De groei raakt de schijf, de back-ups en elke patch die de tabel wijzigt.
- Opties: (a) alles bewaren; (b) een bewaartermijn voor de sync; (c) partities in MariaDB op het commitnummer, maar
  Frappe kent geen partities, en elke `ALTER TABLE` bij een migratie moet er rekening mee houden.
- Voorkeur (b). De site bewaart een ondergrens op de tellerregel: het hoogste commitnummer dat weg is.
  `wijzigingen(sinds)` met `sinds` onder de ondergrens geeft `cursor_te_oud`. De client doet dan een volledige ronde en
  gaat verder met de cursor die hij krijgt. Een nieuwe client (`sinds=0`) doet dat al.
- Veilig verwijderen: nooit een regel met nummer 0. De ondergrens omhoog en de regels eronder weg in dezelfde
  transactie, in delen onder de schrijfgrens (D3). Een lezer ziet dan beide of geen van beide.
- Beslissend: hoe lang de activiteit (`activiteit`, §5.5) terug moet. De versies staan in `Bestandsversie`, en die
  regels gaan nooit weg. Moeten de andere gebeurtenissen jaren blijven (dossier, aansprakelijkheid), dan horen ze in een
  eigen archief, niet in de lijst van de sync. Een verplichte bewaartermijn vond ik niet; dit besluit is aan Ethan.
  Voorstel: 180 dagen voor de sync.
- Kosten: een foutcode, een vlag in `mogelijkheden()`, een opruimtaak in de scheduler (A10), en een besluit over de
  activiteit.
- Bewijs: databaseproeven zoals in §5.4: een cursor net onder, op en boven de ondergrens; verwijderen tijdens een
  lezing; een nieuwe client.

**D3. De veger bij een grote achterstand.** Frappe breekt een transactie af na 200 000 schrijfacties
(`MAX_WRITES_PER_TRANSACTION`, `frappe/database/database.py` r. 99, Frappe 16.55.2; in te stellen met
`max_writes_per_transaction`). De veger nummert alle regels met nummer 0 in één transactie (§5.4, stap 5). Voorbeeld:
's nachts importeert een taak 250 000 bestanden, in delen met een commit ertussen, en geen client leest. De eerste lezer
daarna vindt meer dan 200 000 regels met nummer 0, en zijn veger faalt. De volgende ook, want de achterstand groeit
alleen. Geen client krijgt nog nieuwe regels (VERVOLG-punt van Sol op PR #16).
- Een grote achterstand houdt ook de anderen op. Elke lezer die een regel met nummer 0 ziet, wacht op het tellerslot
  (§5.4, stap 3). Gemeten in de lastproef van P2 (run `d-last-4werkers`, server `197d55b`): bij een achterstand van
  9 846 regels duurt de eerste lezing 2,5 s, bij 40 000 regels 9,0 s en bij 150 000 regels 32,9 s. Alle 20 polls van
  andere clients wachten zo lang mee (32,3 s bij 150 000). Daarna gaat lezen weer snel: de hele lijst van 200 000
  regels vanaf cursor 0 kost 7,1 s in 201 pagina's, en een stille ruimte vanaf 0 kost 0,016 s.
- Oplossing, twee delen. (1) De veger nummert per ronde hoogstens 1 000 regels (ongeveer 0,25 s slot, afgeleid uit de
  meting), in volgorde van `name`, en commit. Hij herhaalt tot er niets over is of tot 2 s om zijn. De rest houdt
  nummer 0 en blijft onzichtbaar; de volgende lezer gaat verder. (2) Een lezer die het slot bezet vindt
  (`FOR UPDATE NOWAIT`), veegt niet en leest meteen. De garantie blijft: elke ronde geeft hogere nummers dan alle
  eerdere, en de regels van de andere veger krijgen een nummer boven elke uitgedeelde cursor, dus de lezer krijgt ze
  bij zijn volgende vraag. Kosten: een lus om stap 3 tot en met 6 van §5.4, en `NOWAIT` in stap 3. Hoort bij stap 4b.
- Bewijs: een test met een achterstand boven de grens, met de grens in de test laag gezet
  (`frappe.db.MAX_WRITES_PER_TRANSACTION = 1000`), zodat hij snel blijft. Zonder de lus faalt die test met
  `TooManyWritesError`. En de lastproef van P2 opnieuw: de polls van anderen wachten niet meer op een grote veger.

**D4. Push, aanwezigheid en meldingen.** §5.4 pusht naar de persoonlijke kamer van elke lezer, zonder inhoud. De
server bepaalt de lezers bij elke aflevering opnieuw, en de client haalt de inhoud zelf op met een gewone aanroep. Per
gebeurtenis kost dat dus N rechtencontroles, N berichten en N ophaalverzoeken, met N het aantal lezers. Rekenvoorbeeld
(schatting): 300 lezers en één gebeurtenis per seconde geven 900 handelingen per seconde voor één ruimte. Bij
aanwezigheid is het zwaarder: kijken 300 mensen naar één bestand, dan vraagt bij elke wisseling iedereen `aanwezig()`,
met elk 300 namen. Bij één wisseling per seconde is dat 90 000 regels per seconde. Daarbij komen 10 HTTP-meldingen per
seconde (300 / 30 s) van `meld_aanwezig`.
- Opties: (a) zoals §5.4 en §5.8; (b) bundelen per gebruiker: hoogstens één push per gebruiker per 2 s ("er is iets
  nieuws"), waarna de client één keer `wijzigingen` vanaf zijn cursor leest (de ronde op de wijzigingen van D1); bij
  aanwezigheid hoogstens één push per bestand per 2 s, en `aanwezig()` geeft de eerste 50 en het aantal; (c) de
  herhaalde melding van aanwezigheid over de realtime-verbinding in plaats van HTTP.
- Voorkeur (b). De veiligheidsregel van §5.4 blijft: geen inhoud in de push, het recht bij de aflevering van de bundel
  en opnieuw bij het ophalen. Kosten: een bundelstap na de commit, in de wachtrij (A10). (c) pas als A2 klopt.
- Bewijs: de lastproef van P2 (`scripts/simulatie/last.py`) telt verzoeken per seconde bij 100 tot 300 clients; met
  push erbij (pas na stap 6) telt hij ook de pushes en de ophaalverzoeken per gebruiker.
- Meldingen: een bestand met 300 volgers en elke minuut een nieuwe versie geeft 300 Notification Logs per minuut
  (432 000 per dag). Voorkeur: automatisch volgen (§5.10, `bron: automatisch`) vervalt na 30 dagen zonder eigen
  handeling. Eén regel per gebeurtenis met een leescursor per gebruiker (zoals de wijzigingenlijst) is de optie voor
  daarna. Waar meldingen wonen, beslist A3 (issue #15).

**D5. Uitchecken per object zonder één slot.** §5.3 bewaart de objecten van een claim als JSON-lijst. Een nieuwe claim
moet met alle lijsten van het bestand vergelijken, onder een slot op het bestand; anders slagen twee overlappende claims
tegelijk. Voorbeeld: 300 mensen met elk 1 000 objecten. Dan leest en vergelijkt elke claim 300 000 GlobalIds, en alle
claims op het bestand wachten op één slot.
- Opties: (a) zoals §5.3; (b) een eigen DocType met één regel per (bestand, GlobalId) en een unieke index: de database
  weigert de tweede claim zelf, zonder slot op het bestand; (c) een claim per groep (een verdieping met alles erin):
  klein, maar dan moet de server de boom van het model kennen (A6).
- Voorkeur (b). Een claim wacht alleen op claims op dezelfde objecten, en het schaalt tot miljoenen regels. De
  DocType krijgt de bescherming van §5.14: de controller weigert wijzigen langs de algemene routes van Frappe. Een claim
  voegt in met `frappe.db.bulk_insert`. Een verlopen claim op hetzelfde object gaat in dezelfde transactie eerst weg.
  Eén claim blijft onder de schrijfgrens van D3 (voorstel: hoogstens 100 000 objecten); groter vraagt (c). Kiest Ethan
  (b), dan vervangt dit het veld `objecten` van `Objectuitcheck` in §5.3. Kosten: een DocType in plaats van een
  JSON-veld; er is nog niets gebouwd, dus er is niets te migreren.
- Bewijs: twee verbindingen claimen tegelijk overlappende lijsten. Eén wint, de ander krijgt `object_uitgecheckt` met
  precies de overlap. Plus een meting van een claim van 100 000 objecten.

**D6. Veel schrijvers op één bestand.** De opslaan-lus probeert hoogstens 5 keer (`LusOpties::max_pogingen`); daarna
maakt de sync een conflictkopie (`motor.rs` r. 963 op `samenwerken/5b-samenvoegen`, `25c20a0`). Eén poging duurt T: hun
downloaden, samenvoegen, uploaden. Komen er λ opslagen per seconde op het bestand, dan slaagt een poging met een kans
van ongeveer e^(−λT) (schatting: niemand anders slaat op tijdens T). Voorbeeld: 20 schrijvers die elk om de 15 minuten
opslaan (λ = 1/45 per seconde):

| Model | T | Kans per poging | Na 5 pogingen een conflictkopie |
|---|---|---|---|
| 100 MB | ongeveer 20 s | 64 % | 0,6 % |
| 1 GB, zoals nu (1 GB downloaden met 100 Mbit/s en samenvoegen) | ongeveer 3 min | 2 % | 91 % |

Uitchecken per object helpt hier niet: ook zonder conflict racen de schrijvers om de versie van het bestand.
- Opties: (a) T klein maken met G1 tot en met G4; (b) een wachtrij op de server: de client uploadt zijn versie met zijn
  basis als voorstel, en de server voegt per bestand één voorstel tegelijk samen met de huidige versie. Niemand verliest
  een race, en niemand doet werk opnieuw. Dit vraagt de bibliotheek op de server (A6). (c) een korte schrijfbeurt: de
  client vraagt vlak voor zijn laatste poging een slot van 2 minuten. (d) federatie (G5): minder schrijvers per bestand.
  (e) een willekeurige wachttijd tussen twee pogingen, die elke keer langer wordt (backoff met jitter).
- Voorkeur: nu (a), (d) en (e); (e) is klein en zit alleen in de client, maar maakt verhongeren alleen minder
  waarschijnlijk, niet onmogelijk. Daarna (b) als A6 klopt: de lastproef hieronder laat zien dat de lus in de client
  alleen niet genoeg is bij veel schrijvers op één bestand. Niet (c): een client die hangt of offline gaat met de
  schrijfbeurt, laat iedereen wachten; (b) geeft dezelfde volgorde zonder dat.
- Bij (b) moeten de client en de server dezelfde versie van de bibliotheek gebruiken, of de server noemt zijn versie in
  het rapport. Anders kan "gelijk" op de server iets anders betekenen dan in de client (§12.3, de canonieke vorm).
- Bewijs: de lastproef van P2 (run `d-last-4werkers`: 4 werkers van gunicorn, kleine bestanden, elke client schrijft
  hetzelfde bestand met 1 s denktijd) laat zien dat de lus vastloopt. Bij 100 schrijvers slaagden in 46 s 25 opslagen,
  tegen 1 154 weigeringen met `verouderde_basis`, en 94 schrijvers slaagden nooit. Bij 300 schrijvers waren het 13
  opslagen, alle van één client. Voorbehoud: veel verzoeken in deze run kregen `401`; dat is nog niet uitgezocht, dus de
  doorvoer is ruw. De verhouding tussen geslaagd en geweigerd staat daar los van.
- De simulatie (P2) telt ook de weigeringen bij een gewone belasting. Run `b-zaad1` (8 clients, 20 beurten, 15 % kans
  dat twee clients tegelijk hetzelfde bestand opslaan): 254 opslagen, alle 254 op de server; 139 keer
  `verouderde_basis` (117 bij `upload_start`, 22 bij `upload_klaar`), 93 samenvoegingen, 16 conflictkopieën. De
  bestanden zijn klein, dus T is kort. Na G1 volgt een meting met N schrijvers op een model van 1 GB.

### 12.3 Duurzaam voor de zeer lange termijn

**Wat de bron is en wat een cache is.** De bron: de IFC-versies in S3 met hun `Bestandsversie`, het samenvoegrapport bij
een versie, de opmerkingen met hun anker, de rechten en de wijzigingenlijst (zolang die bewaard blijft). Al het andere
is daaruit opnieuw te maken: de objectindex, de inhoudshashes, de blokhashes en de aanwezigheid. De regel: een cache
draagt zijn formaatversie, en bij een andere versie maakt de software hem opnieuw. Een cache is nooit de enige plek van
iets.

**Een versie op elk formaat dat we bewaren.**

| Formaat | Waar | Nu | Voorstel |
|---|---|---|---|
| Toestand van de sync | `.openaec/staat.json` (`motor.rs` `laad`, main `a3aceac`) | geen versie; een onleesbaar bestand wordt stil een lege toestand (`unwrap_or_default()`) | een veld `formaat`. Een onbekend, hoger formaat (een oudere app na een downgrade): de sync stopt voor die map met een duidelijke fout en schrijft niets. Wat de sync nu met een lege toestand doet bij een volle map, is niet nagegaan |
| Journaal van vervangingen | `.openaec/journaal/*.json` (stap 5a, `vervang.rs`) | geen versie; een onleesbaar item geeft een fout | een veld `formaat` |
| Objectindex (G1) | `.openaec/`, later misschien bij de versie op de server | — | `formaat`, de versie van de canonieke vorm (wat "gelijk" is: getallen, teksten, vluchtige velden), de hashfunctie, en de BLAKE3 van het IFC-bestand. Klopt er één niet: opnieuw maken |
| Samenvoegrapport | `Bestandsversie.samenvoegrapport` (§5.3) | — | `formaat` en de versie van de bibliotheek; een veld verandert nooit van betekenis |
| Blokken | `Bestandsversie.blokgrootte`, `blokhashes` | de blokgrootte staat per versie (groter boven 80 GiB) | met V2 ook de manier van knippen per versie; oude versies houden hun manier |
| API | `mogelijkheden()` | een vlag per functie | een client vraagt naar functies, niet naar een versienummer. Een veld verdwijnt niet en verandert niet van betekenis; een nieuwe betekenis krijgt een nieuwe naam (zoals `basis_versie_id` naast `basis_versie`) |

**Migratie.** Op de server met Frappe-patches, zoals in stap 4: niets hernummeren, elke herstelling loggen, en een
tweede run verandert niets. In de client leest de sync formaat n en n−1 en schrijft n, via de bestaande weg met een
tijdelijk bestand en een rename (`bewaar`). Een index maakt hij gewoon opnieuw. Let op: een andere canonieke vorm (bijv.
een nieuw vluchtig veld) verandert welke objecten "gewijzigd" zijn bij hetzelfde paar bestanden. Daarom hoort de versie
van de canonieke vorm in de index en in het rapport. Een nieuwe hashfunctie komt in een nieuw veld, niet in het oude.

**Open standaarden en uitstappen.** IFC 4.3 is ISO 16739-1:2024 (fragment:
[iso.org](https://www.iso.org/contents/data/standard/08/41/84123.html), en de aankondiging van buildingSMART van
2 april 2024); het bestandsformaat is STEP (ISO 10303-21). Opmerkingen volgen BCF 3.0 (buildingSMART, geen ISO-norm).
Elke versie in S3 is een volledig bestand (bij een model een gewoon IFC-bestand), en met V2 blijft dat zo. Een ruimte is
dus te exporteren als bestanden met versies plus een `.bcfzip`, en te lezen zonder onze software. IFC5 is in
ontwikkeling (alpha). Het hoofdformaat wordt waarschijnlijk JSON en niet STEP, maar dat is nog geen besluit (fragment:
[forums.buildingsmart.org](https://forums.buildingsmart.org/t/clarifying-the-role-of-ifc-step-spf-in-ifc5-and-its-relationship-with-modern-formats-like-json/6046)).
Het objectmodel (een vaste sleutel per object, attributen per object) blijft passen. Een tweede lezer komt achter
dezelfde functies van de bibliotheek (zie de grenzen hieronder).

**Niet vast aan één transport.** Alles wat een client niet mag missen, staat in de wijzigingenlijst; push en de haak
zijn alleen een signaal (§5.4, §9). Een nieuw transport (WebSocket, SSE, een berichtenbus tussen sites) maakt het dus
sneller, nooit juister. De bibliotheek ziet alleen de trait `Opslag`.

**Ruimte voor live samen bewerken (§5.11).** `verschil` geeft al per object en per attribuut de oude en de nieuwe
waarde. Een live wijziging "attribuut X van object G kreeg waarde W" is dezelfde eenheid. Voorstel: één vorm voor drie
dingen: de uitvoer van `verschil`, de objectdelta van V3, en later een live stroom. Nu bouwen we daar niets voor. De
enige eis voor nu: `verschil` noemt een attribuut bij zijn naam uit het schema, niet bij zijn plek, en een verwijzing
naar een knoop als inhoud, naar een object als GlobalId (zoals §5.6).

**Andere bestandssoorten.** De grens is: `kan_samenvoegen(naam)`, `verschil`, `voeg_samen` en het rapport. Een tweede
formaat (bijv. IFC5, of het eigen formaat van een app) komt achter dezelfde functies; de sync en de server veranderen
niet. DWG, PDF en de rest blijven bij uitchecken per bestand en een conflictkopie (§5.6, "Wat het niet doet"); V2 helpt
ze wel bij het versturen. Een trait voor formaten komt pas als er een tweede formaat is.

**De grenzen tussen de lagen.** De nummers volgen §5.1.

| Laag | Weet | Weet niet | Contract |
|---|---|---|---|
| 1. Opslag (openaec-storage: `Bestandsversie`, S3, blokken, wijzigingenlijst) | bytes, versies (`versie_id`), blokken en hashes, de basis vergelijken, rechten op de plek, de volgorde van gebeurtenissen | wat er in een bestand staat: geen IFC, geen GlobalIds, geen samenvoegen | `versies`, `download(…, versie_id)`, `upload_start` en `upload_klaar` met `basis_bestand`, `basis_versie_id` en `bij_conflict`, `blokken`, `wijzigingen(sinds)` met cursor, `mogelijkheden()` |
| 2. Samenwerkdiensten (server) | mensen, rechten (via `vereis_op_plek` van laag 1), GlobalIds als tekst zonder betekenis (ankers, uitchecken), gebeurtenissen | de inhoud van een IFC-bestand, S3-sleutels en blokken. Heeft hij IFC-kennis nodig (`verschil`, handhaven, een wachtrij), dan roept hij laag 3 aan als los programma: bytes erin, verschil of rapport eruit (A6) | de API van §5.5 en de push; elke gebeurtenis gaat via `gebeurtenis()` in de lijst van laag 1 |
| 3. Formaatkennis (bibliotheek) | IFC en STEP, schema's, objecten, ankers, verschil, samenvoegen, de opslaan-lus | netwerk, database, gebruikers, rechten, de vorm van de API | `kan_samenvoegen`, `verschil`, `voeg_samen`, het rapport; naar de opslag alleen de trait `Opslag` (`huidige`, `lees`, `schrijf_als`) met een bestand- en versie-id zonder betekenis |
| Transport (HTTP nu; later push, WebDAV, browser, werkplek) | hoe een verzoek aankomt | eigen regels | elke weg komt langs dezelfde functies (K10) |

- Afhankelijkheden gaan één kant op. De client (desktop-sync) zet laag 3 samen met een implementatie van `Opslag` voor
  laag 1. Laag 2 gebruikt laag 1 (rechten, gebeurtenissen), en later laag 3 als programma. Laag 1 kent laag 2 en 3 niet:
  laag 2 hoort van laag 1 via de haak en de lijst (§5.4), niet via een directe aanroep. Dat scherpt §5.4 aan: daar
  stuurt `gebeurtenis()` zelf de meldingen (punt 3). Met de meldingen aan de haak kunnen ze ook in de kern wonen (A3)
  zonder dat laag 1 verandert.
- Laag 3 deelt maar twee sleutels met de andere lagen: de GlobalId (met laag 2, die er geen betekenis aan geeft) en het
  versie-id (met laag 1, via de trait; laag 3 geeft er geen betekenis aan).
- Een laag is te vervangen achter zijn contract: een andere S3 voor laag 1, een andere plek voor laag 2 (§9), een ander
  formaat voor laag 3.

### 12.4 Voor Sol

Punten die een review op schaal en duurzaamheid moet toetsen:

1. G1 en G2: klopt de rekensom van het geheugen (5 bytes per instantie, drie indexen, 1,9 GB bij 6,3 GB), en waar
   gaat het geheugen nog heen (verwijzingen naar verderop, gedeelde knopen, het schrijven)? Geeft de weg per object
   precies dezelfde uitkomst als de weg van nu, ook voor de controles die de hele graaf nodig hebben (koppelregel,
   boomregel, kringen)?
2. G1: is het terecht dat een client geen index van een andere client vertrouwt? Kan een index van de basis in
   `.openaec/` verouderen zonder dat de client het merkt?
3. G4, V2: kan hergebruik op hash ooit een verkeerd stuk in een versie zetten (een range die niet klopt, een blok van
   een versie die de gebruiker niet mag lezen), en lekt het iets tussen gebruikers?
4. D1: kan een ronde op de wijzigingen een ander beeld geven dan een volledige ronde, bijv. na een verplaatste map,
   bij een deling als wortel, of bij een soort regel die de client niet kent?
5. D2: kan een client met een cursor net onder, op of boven de ondergrens iets missen? Wat bij een verwijdering tijdens
   een lezing, en bij een deling als wortel?
6. D3: geeft de veger in delen dezelfde garantie als §5.4 (niets gemist, geen dubbel nummer), ook met de tijdgrens en
   twee vegers tegelijk? Is "niet vegen als het slot bezet is" veilig?
7. D4: blijft de regel van §5.4 staan als pushes gebundeld worden, bijv. een bundel die klaarstond vóór een deling
   werd ingetrokken?
8. D5: twee gelijktijdige claims met overlap, een verlopen claim, en de schrijfgrens. Wint er altijd precies één?
9. D6: klopt de schatting e^(−λT)? Verliest een wachtrij op de server ooit stil werk, bijv. door een andere versie van
   de bibliotheek dan in de client?
10. §12.3: een downgrade van de app, een index met een andere canonieke vorm, een rapport in een oud formaat. Gaat er
   iets stil mis?
11. De grenzen tussen de lagen: lekt er ergens kennis over, bijv. GlobalIds in laag 1 of S3 in laag 2?
12. Overengineering: wat in dit hoofdstuk is niet nodig voor de doelen bovenaan?
