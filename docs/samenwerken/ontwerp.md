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
5. **Werkt offline en met Nextcloud.** De samenvoeglogica zit in een bibliotheek die de desktop-sync gebruikt. Die
   werkt met elke opslag die versies en een "schrijf alleen als de versie nog klopt" kent: de SuperCloud en
   Nextcloud.
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
 │ - samenvoegen        │  │ - volgen + meldingen           │  │ Nextcloud:                   │
 │ - opslaan-lus        │  │ - uitchecken per object        │  │   WebDAV, ETag + If-Match,   │
 │   (lees, voeg samen, │  │ - activiteit per bestand       │  │   versies                    │
 │    schrijf)          │  │                                │  │                              │
 └──────────────────────┘  └───────────────────────────────┘  └──────────────────────────────┘
   draait in de client        draait op de server              één trait in de client
   (later ook op de server)   (Frappe-app, plek: zie §9)        (motor.rs `Opslag`)
```

- **Laag 1, opslag**, heeft maar twee dingen nodig: versies lezen en "schrijf deze inhoud alleen als de huidige
  versie nog N is" (vergelijk-en-schrijf). openaec-storage heeft dat met `basis_versie`; Nextcloud met de ETag en
  `If-Match`. Meer hoeft de opslag niet van samenvoegen te weten.
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
| `Bestandsversie` (uitbreiding) | `samengevoegd_uit` (JSON: `{basis, hun}` met versienummers), `samenvoegrapport` (JSON, kort) | Zien dat versie 14 een samenvoeging is van 12 (basis), 13 (hun) en de upload van Piet |
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

1. een push via `frappe.publish_realtime` naar de kamer van het bestand (`doctype="Opslagbestand", docname=…`) en
   van de ruimte. Frappe controleert bij het abonneren het leesrecht met onze `has_permission`;
2. de haak `openaec_storage_gebeurtenis` uit `hooks.py` van andere apps, in de wachtrij (issue #7). Een fout in die
   app houdt de handeling niet tegen;
3. meldingen aan de volgers (§5.10), gebundeld.

Valt het proces uit na de commit, dan is de push of de melding weg, maar de regel staat er. Wie niets mag missen
(een andere app via de haak, de sync), leest de wijzigingenlijst vanaf zijn cursor; het volgnummer van de regel gaat
mee in elke push en elke haakaanroep. Elke weg komt zo langs dezelfde plek: browser, desktop-app, batch, WebDAV en
uploadlink.

**De cursor mag niet voorbij een open transactie schuiven.** Frappe deelt het volgnummer uit bij het invoegen, via
een sequence zonder cache (`frappe/database/sequence.py`), dus in de volgorde van invoegen en niet van committen.
Voorbeeld: transactie A krijgt 41 en blijft open, B krijgt 42 en commit. Een client leest 42 en bewaart cursor 42.
Daarna commit A. `wijzigingen(sinds=42)` vraagt `name > 42` (`api.py` r. 858) en ziet 41 nooit. Dit raakt de
desktop-sync nu al; alleen de volledige ronde elke 10 minuten vangt het op.

Oplossing (stap 4b): een **commitnummer**. `Opslagwijziging` krijgt een veld `commitnummer`. Dat nummer wordt pas
uitgedeeld vlak voor de commit (`frappe.db.before_commit`, `frappe/database/database.py` r. 1199 in Frappe 16):
de transactie vergrendelt één tellerregel voor de site, geeft haar eigen nieuwe regels oplopende nummers, en
commit. De teller blijft vergrendeld tot de commit klaar is. Zo committen de nummers in volgorde: wie nummer N+1
krijgt, wacht tot N gecommit is. Het slot duurt alleen van het uitdelen tot de commit (milliseconden), niet de hele
upload. Een teruggedraaide transactie deelt geen nummer uit, dus er ontstaat geen gat door een rollback.

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
  een lege pagina met een commit vlak erna, een taak die tussendoor commit (elke commit nummert zijn eigen regels;
  een `before_commit`-callback draait per commit maar één keer, dus de nummeraar meldt zich na elke commit opnieuw),
  een batch waarvan één item naar een savepoint terugging, en een andere `before_commit`-callback die na de
  nummeraar nog een regel schrijft.
- Slotvolgorde: een transactie neemt eerst de sloten op bestand of map (stap 4), en het slot op de teller altijd als
  laatste, vlak voor de commit. Zo kan er geen kring van wachtende transacties ontstaan. Hoe lang het slot op de
  teller duurt, hangt af van wat er na het uitdelen nog in de transactie gebeurt; de proef meet het.

### 5.5 API-overzicht

Alles onder `/api/method/openaec_storage.samenwerken.<methode>`, met de inlog van de gebruiker. Rechten volgen
`vereis_op_plek`, dus ook delingen. Namen zijn een voorstel.

| Methode | Recht | Wat |
|---|---|---|
| `meld_aanwezig(bestand, modus, app?, apparaat?, sessie?)` | lezen | Ik heb het bestand open (`bekijken` of `bewerken`). Elke 30 s herhalen. Geeft `sessie` en de lijst van aanwezigen |
| `meld_weg(sessie)` | — | Ik sluit het bestand |
| `aanwezig(bestand)` | lezen | `[{gebruiker, naam, modus, app, apparaat, sinds}]` |
| `opmerkingen(bestand, status?, versie?)` | lezen | Draden met reacties; per draad ook `anker_status` (§5.9) |
| `plaats_opmerking(bestand, tekst, anker?, versie?, draad?)` | lezen (zie keuze K7) | Nieuwe opmerking of reactie; @noemen in de tekst als `@gebruiker@voorbeeld.nl` |
| `wijzig_opmerking`, `verwijder_opmerking` | eigen opmerking | — |
| `los_op(draad)`, `heropen(draad)` | lezen | Status van een draad |
| `volg(doel_doctype, doel, soorten?)`, `ontvolg(…)`, `mijn_volgers()` | lezen | Volgen |
| `checkout_objecten(bestand, objecten, omschrijving?, uren?)` | schrijven | Uitchecken per object; weigert met `object_uitgecheckt` en de lijst als een ander er al een heeft |
| `geef_objecten_vrij(bestand, objecten?)` | eigen uitcheck (of beheerder) | Alles of een deel vrijgeven |
| `uitgecheckte_objecten(bestand)` | lezen | `[{gebruiker, objecten, omschrijving, verloopt}]` |
| `zet_terug(bestand, versie)` | schrijven | Nieuwe versie die gelijk is aan een oude (kopie in S3, geen upload) |
| `verschil(bestand, van_versie, naar_versie)` | lezen | Verschil per object (§5.6); later, als de server de bibliotheek kan draaien |
| `activiteit(bestand, start?, aantal?)` | lezen | Versies, opmerkingen, sloten en delingen van één bestand, nieuwste eerst |

Uitbreiding van de bestaande API:

- `upload_start(…, bij_conflict="kopie" | "weiger", samengevoegd_uit?)`. Met `weiger` geeft `upload_klaar` bij een
  verouderde basis een fout met de foutcode `verouderde_basis` en de huidige versie, in plaats van een
  conflictkopie. Dat is de vergelijk-en-schrijf die de opslaan-lus nodig heeft. `kopie` blijft de standaard, dus
  bestaande clients merken niets.
- `mogelijkheden()` krijgt `samenwerken: {aanwezigheid, opmerkingen, volgen, objectuitcheck, bij_conflict_weiger,
  verschil}` zodat een client weet wat de server kent.

Gebeurtenissen via push (soort en inhoud): `bestand.nieuwe_versie {bestand, versie, door, samengevoegd}`,
`bestand.aanwezigheid {bestand, aanwezigen}`, `bestand.opmerking {bestand, draad, opmerking, door}`,
`bestand.uitgecheckt` en `bestand.vrijgegeven` (slot per bestand), `bestand.objecten_uitgecheckt` en
`bestand.objecten_vrijgegeven {bestand, gebruiker, aantal}`.

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

**Vluchtige velden.** Alleen velden die bij elk opslaan veranderen, tellen niet mee: in `IfcOwnerHistory` de
tijdstempels en de velden over de laatste wijziging, en in de HEADER de tijdstempel. De rest van de keten telt wel
mee. OPS bewaart de auteur en de organisatie van een planning in `IFCPERSON` en `IFCORGANIZATION` achter
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
| Na samenvoegen heeft een object meer relaties dan het schema toestaat (een INVERSE met bovengrens 1, bijv. een wand in twee verdiepingen), terwijl dat in ons en in hun elk klopte | Conflict |
| Na samenvoegen voldoet een samengevoegde verzameling niet aan de grenzen uit het schema (bijv. ons haalt A en hun haalt B uit `RelatedElements = {A, B}`: leeg, terwijl minstens 1 nodig is) | Conflict |
| Een definitie gold in de basis voor een object, de ene kant haalde hem weg (of verwijderde het object), en de andere kant wijzigde die definitie terwijl hij daar nog voor het object gold (zie de koppelregel hieronder) | Conflict |
| Na samenvoegen zit er een kring in de boom (`IfcRelAggregates`, `IfcRelNests`, `IfcRelContainedInSpatialStructure`), of is een object dat in ons én in hun vanaf het project bereikbaar was, dat niet meer | Conflict |
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
van hun komt, krijgt nieuwe nummers achteraan. Zo blijft de delta per blok klein bij het uploaden.

**De opslaan-lus.** Eén trait met drie methodes: `huidige()`, `lees(versie)` en `schrijf_als(basis, inhoud)`, die
weigert als de basis verouderd is. De lus: schrijf; is de basis verouderd, lees basis en huidige, voeg samen,
schrijf opnieuw met de nieuwe basis; geef op na enkele pogingen. openaec-storage en Nextcloud passen allebei op die
trait. Een slot van een ander is een aparte weigering: daar helpt samenvoegen niet, en de lus probeert het niet.

De desktop-sync gebruikt de lus alleen bij een server die `bij_conflict="weiger"` kent (`mogelijkheden()`). Bij een
oudere server blijft alles zoals nu: een conflictkopie. Er is geen tussenweg die een conflictkopie achteraf
samenvoegt en opruimt; zo'n opruimactie kan niet veilig vaststellen dat er niets verloren gaat.

**Wat er bewaard blijft.** De versie van hun en de nieuwe, samengevoegde versie staan op de server en zijn terug te
zetten. De eigen invoer van ons heeft bij een geslaagde samenvoeging nooit als losse versie op de server gestaan. De
desktop-sync bewaart die daarom lokaal (in `.openaec/`, de laatste paar per bestand) voordat hij het lokale bestand
door het resultaat vervangt. Lukt samenvoegen niet, dan gaat ons als conflictkopie naar de server, zoals nu.

**Terugzetten in de lokale map.** De gebruiker kan opnieuw opslaan terwijl de sync samenvoegt. Voorbeeld: de sync
leest de planning met taak A op 2, de gebruiker zet A op 3 en slaat op, en de samengevoegde versie (met A op 2) komt
op de server. Die versie mag de lokale A op 3 niet wegdrukken. Daarom:

1. Samenvoegen werkt met een vaste momentopname S van het lokale bestand (inhoud en hash), bewaard in `.openaec/`.
2. De sync overschrijft nooit een lokaal bestand. Hij verplaatst het eerst naar `.openaec/` (één rename) en
   controleert daar of de inhoud nog S is. Zo ja, dan zet hij het resultaat op de lege plek, met een rename die
   weigert als daar intussen weer een bestand staat. Zo nee, of als die rename weigert, dan zet hij het lokale
   bestand terug (of laat hij het nieuwe staan) en blijft het resultaat alleen op de server.
3. De sync registreert alleen wat hij echt verwerkte. Staat het resultaat lokaal, dan is de toestand "versie N, deze
   inhoud". Bleef de nieuwere lokale versie staan, dan is de toestand "server op N, lokaal afgeleid van S". De volgende
   ronde voegt dan samen met S als basis, het lokale bestand als ons en N als hun. N bevat S al, dus het verschil tussen
   S en N is alleen het werk van de collega. Er ontstaat geen vals conflict met onze eigen tussenstand.
4. Verplichte proef in stap 5: lezen, de gebruiker slaat opnieuw op, de upload slaagt, terugzetten. Daarna staan
   zowel de wijziging van de collega als de laatste lokale wijziging in de volgende versie, en nergens is een lokaal
   bestand verloren.

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

### 5.8 Aanwezigheid

Een client meldt `meld_aanwezig` bij het openen en daarna elke 30 seconden. Na 90 seconden zonder melding vervalt de
sessie. De server bewaart sessies alleen in Redis, niet in de database: het is vluchtige data. Bij elke wijziging in
de lijst stuurt de server `bestand.aanwezigheid` naar de kamer van het bestand. De desktop-sync kan later ook
aanwezigheid melden voor een bestand dat lokaal open staat (bijv. via de Office-lockbestanden `~$naam` of een slot
van het besturingssysteem).

### 5.9 Opmerkingen

Een draad hoort bij een bestand en een versie. Het `anker` zegt waar in het bestand:

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

### 5.12 Opslag via Nextcloud

De Nextcloud-kolom is eigen kennis; ik heb hem niet tegen een echte Nextcloud getest.

| Nodig | openaec-storage | Nextcloud |
|---|---|---|
| Versie lezen | `download(bestand, versie)` | WebDAV `versions/<gebruiker>/versions/<fileid>` |
| Vergelijk-en-schrijf | `basis_versie` (en straks `bij_conflict=weiger`) | `PUT` met `If-Match: <ETag>`; 412 bij een andere versie |
| Uitchecken per bestand | `vergrendel` | WebDAV `LOCK` of de app files_lock |
| Opmerkingen | `Opslagopmerking` (§5.9) | WebDAV `comments/files/<fileid>` (zonder anker en status) |
| Activiteit en meldingen | wijzigingenlijst, Notification Log | apps activity en notifications (OCS) |
| Aanwezigheid, uitchecken per object | eigen dienst | bestaat niet; alleen via een eigen dienst |

Gevonden in openaec-cloud (de Rust-bibliotheek voor Nextcloud): `upload_file` en `upload_manifest` doen een `PUT`
zonder `If-Match` (`src/webdav.rs`). Twee schrijvers overschrijven elkaar dan zonder melding. Wie die bibliotheek
gebruikt voor samenwerken, moet de ETag meesturen.

### 5.13 Offline

De desktop-sync werkt offline door. Bij weer online gaat elke upload door de opslaan-lus: samenvoegen waar het kan,
een conflictkopie met rapport waar het niet kan. Uitgecheckte objecten bewaart de client in zijn stand, zodat hij ook
offline kan waarschuwen. Aanwezigheid en meldingen zijn er offline niet.

### 5.14 Rechten en veiligheid

- Alles volgt `vereis_op_plek`: rol in de ruimte of deling, gearchiveerde ruimte alleen lezen, `buiten_deling`.
- Een deellink zonder account (soort `link`) kan geen opmerkingen plaatsen en ziet geen aanwezigheid: er is geen
  persoon om te tonen of te melden.
- De samenvoegbibliotheek leest onbetrouwbare invoer. Ze mag nooit vastlopen of crashen op een kapot bestand, en ze
  heeft grenzen voor diepte en grootte.
- Een samengevoegde versie is een gewone nieuwe versie. De versie van hun blijft bestaan en is terug te zetten. De
  eigen invoer van ons staat lokaal bewaard (§5.6, "Wat er bewaard blijft"), of als conflictkopie op de server.

## 6. Keuzes

Alle keuzes: genomen door Claude (Opus 5.5) binnen de opdracht van Ethan van 5 november 2026, nog niet beoordeeld.

| # | Keuze | Reden | Alternatief dat het niet werd |
|---|---|---|---|
| K1 | Samenvoegen op GlobalId en inhoud, niet op STEP-nummer | OPS en andere programma's nummeren bij elk opslaan opnieuw; de GlobalId blijft (IFC eist dat) | ifcmerge-aanpak op `#nummers`: werkt alleen met programma's die de nummers laten staan (Bonsai) |
| K2 | Eigen STEP-lezer in Rust, geen IfcOpenShell | Moet in de desktop-app en later in de browser (WebAssembly) draaien, zonder C++-afhankelijkheid; we hebben alleen de graaf nodig, geen geometrie | IfcOpenShell (Python/C++): zwaar om mee te leveren in Tauri en in de browser |
| K3 | Samenvoegen eerst in de client, later ook op de server | Werkt offline en met Nextcloud; de server hoeft alleen vergelijk-en-schrijf te kennen | Alleen op de server: werkt niet met Nextcloud en vraagt eerst een besluit over de kern |
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
| K16 | Een commitnummer, uitgedeeld vlak voor de commit onder een kort slot op één tellerregel | Volgnummers komen nu in invoegvolgorde, niet in commitvolgorde, en een client kan regels missen; zo volgt de veiligheid uit de commitvolgorde, en blijven paginering en bestaande cursors werken (review Sol, ronde 2 en 3) | Een cursor die vóór een jong gat stopt: veilig alleen als geen transactie langer duurt dan de tijdgrens (een taak in `long` mag 25 minuten), en hij breekt de paginering |
| K17 | Gedeelde ankers krijgen de verzameling van hun ankers als identiteit; twijfel is een conflict | Een gekozen vertegenwoordiger verspringt als een programma een plaatsing splitst (review Sol) | Een vaste keuze, bijv. het kleinste anker |
| K18 | Na samenvoegen de boom controleren: geen kring, niets wat bereikbaar was raakt los | Twee geldige herindelingen kunnen samen een kring maken, terwijl elke cardinaliteit klopt (review Sol, ronde 3) | Alleen cardinaliteiten uit het schema controleren |
| K12 | Live samen bewerken (C) pas later | Vraagt dat elke app zijn model als wijzigingen per object uitdrukt, en een besluit over het transport | Nu al een sessiedienst: zonder app die hem gebruikt, is hij niet te testen |

## 7. Volgorde van bouwen

Elke stap is een eigen draft-PR die op zichzelf werkt, met een oordeel van Sol (GPT-6.1-Sol).

| Stap | Wat | Repo | Hangt af van de kern? |
|---|---|---|---|
| 1 | Dit ontwerp | openaec-storage | Nee |
| 2 | Bibliotheek: IFC lezen, objectmodel, verschil | openaec-cloud-desktop | Nee |
| 3 | Bibliotheek: samenvoegen, schrijven, opslaan-lus, met een proef van 8 clients tegelijk in het geheugen | openaec-cloud-desktop | Nee |
| 4 | Storage: een nieuwe versie vastleggen onder een slot op het bestand of de map (§2), `bij_conflict=weiger`, `samengevoegd_uit` | openaec-storage | Nee, maar de owner beoordeelt de API |
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

De kern (`openaec-supercloud`) is niet gelezen. Daarom staan deze aannames open. Elke aanname kan de plek van de code
of de API veranderen.

| # | Aanname | Wat er verandert als hij niet klopt |
|---|---|---|
| A1 | openaec-storage is de eigenaar van bestanden en versies; de werkplek (`openaec_werkplek`) toont tools en geeft berichten door (zie issues #2, #3, #6) | Opmerkingen, aanwezigheid en volgen kunnen in de kern horen |
| A2 | De SuperCloud draait socket.io van Frappe, en de desktop-app kan daar met een token bij | Push (§5.4) moet via een andere dienst, of de clients blijven vragen |
| A3 | De kern heeft nog geen eigen dienst voor opmerkingen, meldingen of activiteit | Dan bouwen we daarop in plaats van op eigen DocTypes |
| A4 | Gebruikers en gasten beheert openaec_beheer; rechten op bestanden gaan alleen via `rechten.py` | Rechten voor opmerkingen kunnen anders liggen |
| A5 | Het ontwerp van openaec-storage (`docs/superpowers/specs/2026-10-20-openaec-storage-design.md` in de kern) zegt over conflicten wat de code zegt (§6 daarvan: de upload wint alleen bij de juiste basis) | De conflictregel kan breder bedoeld zijn |
| A6 | Naast Frappe kan een Rust-hulpprogramma draaien (zoals de WebDAV-gateway al in een eigen container draait) | `verschil` en handhaving op de server moeten anders, bijv. als Python-extensie |
| A7 | De `BIM Issue` van open-bim-platform (BCF) en `Opslagopmerking` worden één systeem | Twee systemen voor opmerkingen op hetzelfde model |
| A8 | Het werkplekprotocol kan gebeurtenissen naar een tool sturen (voorstel in issue #3) | Aanwezigheid en samenvoegen in de browser-apps hebben een andere weg nodig |

Zodra de kern leesbaar is, lees ik eerst die repo en pas ik dit ontwerp en de plek van de code aan.

## 10. Issues voor de owner van de kern

Deze komen als issues in openaec-storage, zoals #2 tot en met #7:

1. Werkplek: aanwezigheid en gebeurtenissen van een bestand aan een tool doorgeven.
2. Werkplek: samenvoegen bij opslaan vanuit een tool (bibliotheek in WebAssembly of op de server).
3. Realtime in de SuperCloud: draait socket.io, en kan de desktop-app er met een token bij?
4. Server: een Rust-hulpprogramma naast Frappe kunnen draaien voor `verschil` en samenvoegen.
5. Eén systeem voor opmerkingen op bestanden en BIM-issues (BCF).
6. Meldingen: één plek voor meldingen en e-mailbundels in de SuperCloud.

De links komen hier zodra de issues er zijn.

## 11. Bronnen

- openaec-storage, `main` op commit `e87b870`: `README.md`, `openaec_storage/api.py`, `rechten.py`, `delen.py`,
  `dossier.py`, `hooks.py`.
- openaec-cloud-desktop, `main`: `crates/openaec-sync/src/motor.rs`, `opslag.rs`, `api.rs`, `src-tauri/src/bewaking.rs`,
  `src-tauri/src/sloten.rs`.
- open-planner-studio: `src/services/ifc/ifcWriter.ts` (GlobalIds en nummering), `examples/*.ifc`, en het ontwerp
  in draft-PR #797 (`docs/relaties-tussen-projecten.md`, branch `docs/relaties-tussen-projecten-supercloud`).
- open-bim-platform: `docs/superpowers/specs/2026-10-26-open-bim-platform-design.md` (§5.6 BIM Issue en BCF, §6.2
  ifcmerge, §7.4 eisen aan storage).
- openaec-cloud (Nextcloud): `src/webdav.rs`, `src/lib.rs`.
- Microsoft 365 en AEC-tools: de links in §3.
