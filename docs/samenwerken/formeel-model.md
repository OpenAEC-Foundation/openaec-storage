# Het samenwerkprotocol, formeel nagegaan

Stand: 5 november 2026. Voor: Ethan. Over: de sync tussen de desktop-app en de SuperCloud-server.

## In het kort

Ik heb het samenwerkprotocol nagebouwd als een wiskundig model (TLA+). Een programma (de model checker TLC) liep
daarna alle volgordes af waarin clients, server en netwerk kunnen handelen, binnen vaste grenzen. Dat vond vier
fouten die nog niet bekend waren, en bevestigde dat de bekende fouten echt zijn en dat de geplande fixes ze oplossen.
De technische kant (bronnen, aannames, hoe je het herhaalt) staat in [`formeel/README.md`](formeel/README.md).

## Wat is een formeel model, en wat zegt het?

Een test probeert één volgorde van gebeurtenissen. Een model checker probeert ze allemaal. Voorbeeld: Anna en Piet
slaan allebei op, en bij Piet valt het netwerk weg. Een test kiest één moment voor die storing. TLC probeert elk
moment: vóór het verzoek, na het verzoek maar vóór het antwoord, midden in een ronde van een andere client.

Het model is een vereenvoudiging van de code. Wat TLC vindt, is dus een fout in het model. Elke fout hieronder heb ik
daarna in de echte code nagelopen. Wat TLC níet vindt, geldt alleen binnen de grenzen van de run (bijvoorbeeld 2 of 3
clients, 1 of 2 bestanden, hoogstens 2 netwerkstoringen). Fouten die pas bij grotere aantallen optreden, ziet TLC
niet.

## Wat is nagegaan

Het protocol heeft vier delen. Elk deel is een eigen model.

1. **De wijzigingenlijst.** Een client vraagt de server: "wat is er veranderd sinds mijn laatste keer?". De server
   geeft een lijst en een cursor. De vraag: mist een client nooit een wijziging?
2. **Uitchecken.** Een gebruiker zet een slot op een bestand. De vraag: heeft hoogstens één gebruiker het slot, en
   legt niemand anders intussen een versie vast?
3. **Uploaden op de server.** De client stuurt een bestand in delen en zegt dan "klaar". De server vergelijkt met de
   versie waarop de client werkte, en maakt een nieuwe versie of een conflictkopie. De vraag: gaat er nooit een
   versie stil verloren, ook niet als een verzoek twee keer komt of de server halverwege uitvalt?
4. **De desktop-sync.** Twee of drie clients, met gebruikers die opslaan en verwijderen, en een netwerk dat
   verzoeken en antwoorden kwijtraakt. De vraag: gaat er nooit werk verloren zonder conflictkopie of melding, en
   komen alle clients uiteindelijk op dezelfde stand als het netwerk weer werkt?

Elk deel is nagegaan voor de code zoals hij nu op main staat, en voor de PR's die het samenwerken verbeteren:
storage-PR #9 (vergelijk-en-schrijf), storage-PR #16 (commitnummer), desktop-PR #8 (veilig vervangen, stap 5a) en
desktop-PR #9 (samenvoegen in de sync, stap 5b).

Daarnaast is er een bewijsschets voor het samenvoegen van IFC-bestanden: [`formeel/samenvoegen-bewijs.md`](formeel/samenvoegen-bewijs.md).

## Wat klopt

- **De wijzigingenlijst met storage-PR #16.** Geen enkele volgorde laat een client een wijziging missen. En een
  client die blijft vragen, krijgt uiteindelijk elke wijziging die voor hem is. TLC liep 193.723 toestanden af (3
  gelijktijdige schrijvers met elk 1 regel, 2 lezers, 2 ruimtes; 39 minuten) en 643.565 toestanden (2 schrijvers met
  elk 2 regels en een savepoint; 42 minuten). Als ik de belangrijkste stap van de fix weghaal, vindt
  TLC meteen een fout. Dat laat zien dat het model de fix echt meet.
- **Uploaden met storage-PR #9, als de client de opslaan-lus gebruikt.** Geen dubbele actieve bestanden, geen stille
  overschrijving, geen verloren inhoud (42.920 toestanden), zolang S3 een tweede "klaar" voor dezelfde upload weigert.
- **Uitchecken, met een kleine aanpassing** (zie fout 1). Met die aanpassing: geen fout in 5.969 toestanden.
- **De desktop-sync van stap 5a en 5b, met één aanpassing** (zie fout 2): geen verloren werk (5a: 81.520, 5b:
  88.308 toestanden, 2 clients, 1 bestand).
- **De bekende fouten op main bestaan echt**, en de geplande PR's lossen ze op in het model: de wijzigingenlijst die
  regels overslaat (PR #16), twee bestanden met dezelfde naam en stil overschrijven bij een upload (storage-PR #9),
  en een download die een net opgeslagen bestand overschrijft (desktop-PR #8).

## De fouten, als verhaal

### Fout 1. Twee mensen kunnen tegelijk een bestand uitchecken

Anna en Piet klikken allebei op "Uitchecken" voor `gevel.ifc`, bijna tegelijk. De server kijkt voor Piet of er al
een slot is. Dat ziet hij nog niet: Anna's slot is op dat moment nog niet vastgelegd. Dan legt de server Anna's slot
vast. Daarna wist de server voor Piet "alle sloten op dit bestand" en zet het slot van Piet. Anna en Piet krijgen
allebei "vergrendeld". Anna denkt dat ze het bestand alleen heeft, maar het slot is van Piet.

- Waar: `vergrendel` in `openaec_storage/api.py` (main en storage-PR #9, r. 1455-1479). De controle leest zonder slot
  op de regel; daarna wist hij alle sloten en voegt hij er een in.
- Bij een andere database-instelling (`innodb_snapshot_isolation`) gaat het iets anders mis: dan krijgt Piet een
  fout, maar een upload van een derde kan een versie vastleggen terwijl Anna het slot net heeft.
- Fix: `vergrendel` en `ontgrendel` in een eigen transactie, en eerst de bestandsregel op slot zetten (`FOR UPDATE`),
  net als de upload dat al doet. Met die fix vindt TLC geen fout meer.
- Model: `Slot.tla`, runs `Slot_RR_nu`, `Slot_RC_nu`, `Slot_RR_SI_nu` (fout) en `Slot_RR_fix`, `Slot_RC_fix` (geen
  fout).

### Fout 2. Een verwijdering wist het werk van een ander

Piet ruimt zijn map op en verwijdert `fundering.ifc`. Hij kende versie 1. Intussen slaat Anna een wijziging op;
versie 2 staat op de server. Piets sync-ronde ziet: lokaal weg, en de laatst bekende versie was 1. De client vraagt
de server het bestand te verwijderen. De server vraagt niet welke versie Piet bedoelde, en zet versie 2 in de
prullenbak. Daarna ziet Anna's sync: het bestand is weg op de server, en lokaal is het niet veranderd sinds versie 2.
Haar client verwijdert ook haar lokale kopie. Anna's werk staat nu alleen nog in de prullenbak. Niemand krijgt een
melding.

- Waar: desktop `motor.rs` (main r. 660-669 en r. 1008-1013, en net zo in 5a en 5b): `verwijder(bestand)` zonder
  versie. Server: `verwijder` in storage-PR #9 (r. 874-885) heeft geen basis.
- Fix: de client stuurt de versie mee die hij verwijdert, en de server weigert als dat niet de huidige versie is. De
  client haalt dan de nieuwe versie op. Met die fix vindt TLC geen fout (stap 5a en 5b).
- Model: `SyncClient.tla`, runs `Sync_5a_k2p1`, `Sync_5b_k2p1` (fout) en `Sync_5a_k2p1_fix`, `Sync_5b_k2p1_fix`.

### Fout 3. Een upload met alleen een versienummer kan het verkeerde bestand overschrijven

Anna werkt een dag offline aan `planning.xlsx`. Ze kende versie 1. Intussen vervangt Piet het bestand: hij
verwijdert het en zet een nieuw `planning.xlsx` op dezelfde plek. Dat nieuwe bestand heeft ook versie 1. Anna komt
weer online. Haar client ziet: lokaal gewijzigd, en op de server staat een ander bestand. Hij uploadt met "mijn basis
is versie 1", zonder te zeggen welk bestand. De server ziet op die plek Piets nieuwe bestand, met huidige versie 1, en
legt Anna's inhoud vast als versie 2. Er komt geen conflictkopie. Piets bestand staat nog in de geschiedenis, maar
niemand merkt dat het overschreven is.

- Waar: storage-PR #9 kan het wel goed doen: hij vergelijkt het bestand als de client `basis_bestand` meestuurt. De
  desktop stuurt dat alleen in de opslaan-lus voor IFC (5b, `api.rs` r. 741-753). Een gewone upload stuurt alleen
  het nummer (5b `motor.rs` r. 1709-1712), op main, in 5a en in 5b. Voor IFC met samenvoegen kijkt 5b wel naar het
  bestand (r. 910); daar komt een conflictkopie.
- Hoe vaak: het vraagt een bestand dat vervangen wordt (verwijderen en opnieuw zetten) terwijl een ander er nog
  lokaal aan werkt, en hetzelfde versienummer. Een nieuw bestand begint altijd bij versie 1, dus dat laatste is niet
  zeldzaam.
- Fix: de desktop stuurt bij elke upload `basis_bestand` mee als de server dat kent.
- Model: `UploadServer.tla`, run `UploadServer_pr9_voltooi_eenmalig`.

### Fout 4. Een herhaald "klaar" kan de inhoud van een vastgelegde versie wissen

Anna's client stuurt "klaar" voor een upload. De server legt de nieuwe versie vast. Net daarna, vóór de server het
uploadbewijs (het token) heeft gewist, valt de worker uit. Anna's client krijgt geen antwoord en stuurt "klaar"
opnieuw, met hetzelfde token. Op S3-opslag die een tweede "klaar" ook als gelukt meldt (Ceph RGW wel; AWS S3 en MinIO
waarschijnlijk niet), loopt de server de upload nog eens af. Nu is Anna's eigen versie de huidige, dus haar basis is
verouderd. Bij "weiger" ruimt de server de upload op en wist hij het S3-object: dat is de inhoud van de versie die al
vastligt. Bij "conflictkopie" wijzen twee versies naar hetzelfde object.

- Waar: `_rond_upload_af` en `_ruim_upload_op` in storage-PR #9 (r. 674-701): het token gaat pas na de commit weg, en
  opruimen kijkt niet of een versie het object al gebruikt.
- De desktop-sync stuurt "klaar" niet twee keer met hetzelfde token. Andere clients mogen dat wel; het commentaar bij
  `een_afronding` gaat daarvan uit.
- Fix: bij een tweede "klaar" eerst kijken of een versie dit S3-object al heeft, en dan die versie teruggeven. Of het
  token wissen in dezelfde stap als de commit.
- Model: `UploadServer.tla`, runs `UploadServer_pr9_inhoud` en `UploadServer_pr9_lus_voltooi_herhaalbaar`.

### Bekende fouten, bevestigd

- **De wijzigingenlijst op main slaat regels over.** Schrijver 1 voegt een wijziging toe, maar zijn transactie loopt
  nog. Schrijver 2 voegt er een toe en is eerder klaar. Een client leest de lijst, krijgt de wijziging van schrijver
  2 en zet zijn cursor daarop. Dan is schrijver 1 klaar. Zijn wijziging heeft een lager nummer dan de cursor; de
  client krijgt haar nooit. Storage-PR #16 lost dit op (zie "Wat klopt").
- **Main: twee bestanden met dezelfde naam**, en een upload op een oude basis die stil overschrijft. Storage-PR #9
  lost het op.
- **Main-desktop: een download overschrijft een net opgeslagen bestand.** Desktop-PR #8 lost het op.

## Samenvoegen van IFC

De bibliotheek voegt samen per "register": per object, per eigenschap, per lid van een verzameling. Per register is
de regel eenvoudig: wie iets veranderde, wint; veranderden beide het anders, dan is het een conflict. Voor die regel
heb ik bewezen dat hij geen wijziging stil laat vallen, dat de volgorde van ons en hun niet uitmaakt, en dat nog eens
samenvoegen niets verandert. De opslaan-lus (opnieuw samenvoegen na een weigering) geeft hetzelfde als in één keer,
of is strenger.

Of dat ook voor echte IFC-bestanden geldt, hangt af van drie voorwaarden: hetzelfde ding moet in elke versie
hetzelfde register krijgen, het geschreven bestand moet zijn wat het plan zegt, en de STEP-nummering mag niet
meetellen. Die voorwaarden toetsen property-based tests op de bibliotheek (eigen draft-PR op de branch van
desktop-PR #7). De uitkomst volgt hieronder zodra de tests klaar zijn.

## Aannames: wat het model niet ziet

- **Grenzen.** 2 of 3 clients, 1 of 2 bestanden, een paar bewerkingen en hoogstens 2 netwerkstoringen per run.
- **Inhoud als lijst van bewerkingen.** Elke keer opslaan voegt een bewerking toe. Zo is "verlies" eenvoudig te
  zien: een bewerking die nergens meer staat.
- **Samenvoegen is een vereniging of een conflict.** Het model kijkt niet in IFC; dat doet de bewijsschets.
- **Het kenmerk is de inhoud.** De echte client vergelijkt grootte en wijzigingstijd. Twee versies met dezelfde
  grootte en tijd ziet de echte client als gelijk; het model niet.
- **De client crasht niet** midden in een ronde. Het netwerk wel.
- **Geen hernoemen of verplaatsen.** Elk pad staat los.
- **De database werkt zoals MariaDB** bij de standaard-isolatie (REPEATABLE READ), en S3 zoals beschreven bij fout 4.
- **Een programma dat het bestand open houdt** (en bij opslaan zijn oude kopie terugschrijft) staat standaard uit.
  Het ontwerp noemt dat al een grens die blijft.

## De runs

| Model | Toestanden | Uitkomst |
|---|---|---|
| Wijzigingenlijst, main | 6.648 | fout: slaat over |
| Wijzigingenlijst, PR #16 | 193.723 en 643.565 | geen fout, ook levend |
| Uitchecken, nu | 130-352 | fout 1 |
| Uitchecken, met fix | 5.969 | geen fout |
| Upload, main | 312 | twee bestanden met dezelfde naam |
| Upload, PR #9, opslaan-lus | 42.920 | geen fout |
| Upload, PR #9, alleen nummer | 5.449 | fout 3 |
| Upload, PR #9, herhaald klaar | 65-1.722 | fout 4 |
| Sync 5a / 5b, 2 clients | volgt | fout 2 |
| Sync 5a / 5b, met fix | 81.520 / 88.308 | geen fout |

Een fout wordt meestal snel gevonden; dan stopt TLC. Daarom zijn die getallen klein. Alle runs en logboeken staan in
[`formeel/runs/`](formeel/runs/).

## Open vragen

(volgt na de review)
