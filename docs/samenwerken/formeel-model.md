# Het samenwerkprotocol, formeel nagegaan

Stand: 5 november 2026, na review ronde 1. Voor: Ethan. Over: de sync tussen de desktop-app en de SuperCloud-server.

## In het kort

Ik heb het samenwerkprotocol nagebouwd als een wiskundig model (TLA+). Een programma (de model checker TLC) liep
daarna alle volgordes af waarin clients, server en netwerk kunnen handelen, binnen vaste grenzen. Dat vond vier
fouten. Drie waren nog niet bekend; de vierde (fout 2) vond een review van desktop-PR #11 dezelfde dag ook. Het model
bevestigde ook dat de bekende fouten echt zijn en dat de geplande fixes ze oplossen.
Een tweede model (Sol) las het model en de code na; zijn bevindingen zijn verwerkt. De technische kant (bronnen,
aannames, hoe je het herhaalt) staat in [`formeel/README.md`](formeel/README.md).

## Wat is een formeel model, en wat zegt het?

Een test probeert één volgorde van gebeurtenissen. Een model checker probeert ze allemaal. Voorbeeld: Anna en Piet
slaan allebei op, en bij Piet valt het netwerk weg. Een test kiest één moment voor die storing. TLC probeert elk
moment: vóór het verzoek, na het verzoek maar vóór het antwoord, midden in een ronde van een andere client.

Het model is een vereenvoudiging van de code. Wat TLC vindt, is dus eerst een fout in het model. Elke fout hieronder
is daarna in de echte code nagelopen, door mij en door de reviewer. Wat TLC níet vindt, geldt alleen binnen de
grenzen van de run (bijvoorbeeld 2 of 3 clients, 1 of 2 bestanden, hoogstens 1 netwerkstoring) en alleen voor wat
het model nabootst. Een groene run is dus geen bewijs dat de code klopt; een rode run met een verhaal dat in de code
terug te vinden is, is wel een echte fout.

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
  elk 2 regels en een savepoint; 42 minuten). Als ik de belangrijkste stap van de fix weghaal, vindt TLC meteen een
  fout. Dat laat zien dat het model de fix echt meet.
- **Uploaden met storage-PR #9, als de client de opslaan-lus gebruikt.** Geen dubbele actieve bestanden, geen stille
  overschrijving, geen verloren inhoud (42.920 toestanden), zolang S3 een tweede "klaar" voor dezelfde upload weigert.
- **Uitchecken, met een kleine aanpassing** (zie fout 1). (Aantallen volgen; de run loopt opnieuw na de review.)
- **De desktop-sync van stap 5a en 5b, met één aanpassing** (zie fout 2). (Aantallen volgen; de runs lopen opnieuw
  na de review.)
- **De bekende fouten op main bestaan echt**, en de geplande PR's lossen ze op: de wijzigingenlijst die regels
  overslaat (PR #16), twee actieve bestanden met dezelfde naam (storage-PR #9), en een download die een net
  opgeslagen bestand overschrijft (desktop-PR #8).

## De fouten, als verhaal

De reviewer (Sol) deelt alle vier nieuwe fouten in als **BLOKKEERT**: er kan werk stil verloren gaan, of een planner
loopt er in gewoon werk tegenaan.

### Fout 1. Twee mensen kunnen tegelijk een bestand uitchecken

Anna en Piet klikken allebei op "Uitchecken" voor `gevel.ifc`, bijna tegelijk. De server kijkt voor Piet of er al
een slot is. Dat ziet hij nog niet: Anna's slot is op dat moment nog niet vastgelegd. Dan legt de server Anna's slot
vast. Daarna wist de server voor Piet "alle sloten op dit bestand" en zet het slot van Piet. Anna en Piet krijgen
allebei "vergrendeld". Anna denkt dat ze het bestand alleen heeft, maar het slot is van Piet.

- Waar: `vergrendel` in `openaec_storage/api.py` (main en storage-PR #9, r. 1455-1479). De controle leest zonder slot
  op de regel; daarna wist hij alle sloten en voegt hij er een in.
- Bij een andere database-instelling (`innodb_snapshot_isolation`, standaard aan vanaf MariaDB 11.6.2) gaat het iets
  anders mis: dan krijgt Piet een fout, maar een upload van een derde kan een versie vastleggen terwijl Anna het slot
  net heeft. Welke instelling de server heeft, is niet nagegaan.
- Fix: `vergrendel` en `ontgrendel` in een eigen transactie, en eerst de bestandsregel op slot zetten (`FOR UPDATE`),
  net als de upload dat al doet; daarna het slot opnieuw lezen. Getoetst tegen vergrendelen, ontgrendelen, verlopen,
  forceren en uploaden; niet tegen verwijderen of verplaatsen.
- Gemeld: [openaec-storage#20](https://github.com/OpenAEC-Foundation/openaec-storage/issues/20).
- Model: `Slot.tla`. In de eerste versie kon een slot niet verlopen (een fout in het model, gevonden door de
  reviewer). Dat is hersteld; de runs lopen opnieuw.

### Fout 2. Een verwijdering wist het werk van een ander

Piets sync-ronde begint. De lijst van de server toont `fundering.ifc` op versie 1, en Piet heeft het lokaal net
verwijderd. Zijn client besluit: verwijder het ook op de server. Op dat moment slaat Anna een wijziging op; versie 2
komt op de server. Dan stuurt Piets client het verwijderverzoek. De server vraagt niet welke versie Piet bedoelde, en
zet het bestand, met Anna's versie 2, in de prullenbak. Daarna ziet Anna's sync: het bestand is weg op de server, en
lokaal is het niet veranderd sinds versie 2. Haar client verwijdert ook haar lokale kopie. Anna's werk staat nu alleen
nog in de prullenbak. Niemand krijgt een melding.

- Het venster is de tijd tussen Piets lijst en zijn verwijderverzoek. Ziet Piets lijst al versie 2, dan haalt zijn
  client het bestand terug in plaats van het te verwijderen. In de code komen alle verwijderingen aan het eind van
  de ronde, na alle uploads en downloads; het venster kan dus lang zijn.
- Waar: desktop `motor.rs` (main r. 660-669 en r. 1008-1013, en net zo in 5a en 5b): `verwijder(bestand)` zonder
  versie. Server: `verwijder` in storage-PR #9 (r. 874-885) heeft geen basis.
- Fix: de client stuurt de versie mee die hij verwijdert, en de server weigert als dat niet de huidige versie is. De
  vergelijking en het verwijderen moeten onder hetzelfde slot op de bestandsregel gebeuren; een losse controle
  vooraf verplaatst de race alleen. Weigert de server, dan haalt de client de nieuwe versie op.
- Ook bekend: desktop-PR #11 (offline wachtrij) noemt hetzelfde gat als knownbugs 27, en wacht op "optie E"
  (verwijderen met een basis op de server). Wat het model toevoegt: de tweede stap (Anna's eigen sync verwijdert ook
  haar lokale kopie, dus niemand merkt het), en dat een controle vlak vóór het verwijderen het gat niet sluit.
- Gemeld: [openaec-storage#21](https://github.com/OpenAEC-Foundation/openaec-storage/issues/21) (server) en
  [openaec-cloud-desktop#14](https://github.com/OpenAEC-Foundation/openaec-cloud-desktop/issues/14) (client).
- Model: `SyncClient.tla`, runs `Sync_5a_k2p1`, `Sync_5b_k2p1` (fout) en `*_fix` (met de fix).

### Fout 3. Een upload met alleen een versienummer kan het verkeerde bestand overschrijven

Anna werkt een dag offline aan `planning.xlsx`. Ze kende versie 1. Intussen vervangt Piet het bestand: hij
verwijdert het en zet een nieuw `planning.xlsx` op dezelfde plek. Dat nieuwe bestand heeft ook versie 1. Anna komt
weer online. Haar client ziet: lokaal gewijzigd, en op de server staat een ander bestand. Hij uploadt met "mijn basis
is versie 1", zonder te zeggen welk bestand. De server ziet op die plek Piets nieuwe bestand, met huidige versie 1, en
legt Anna's inhoud vast als versie 2. Er komt geen conflictkopie. Piets bestand staat nog in de geschiedenis, maar
niemand merkt dat het overschreven is.

- Waar: storage-PR #9 kan het wel goed doen: hij vergelijkt het bestand als de client `basis_bestand` meestuurt. De
  desktop stuurt dat alleen in de opslaan-lus voor IFC (5b, `api.rs` r. 741-753). Een gewone upload, een kleine
  upload en het inleveren van een bron sturen alleen het nummer (5b `motor.rs` r. 1709-1712), op main, in 5a en in
  5b. Voor IFC met samenvoegen kijkt 5b wel naar het bestand (r. 910); daar komt een conflictkopie.
- Hoe vaak: een bestand vervangen terwijl een ander er nog offline aan werkt, is gewoon werk. Versie 1 tegenover
  versie 1 is genoeg, en een nieuw bestand begint altijd bij versie 1.
- Fix: de desktop stuurt bij elke upload `basis_bestand` mee als de server dat kent.
- Gemeld: [openaec-cloud-desktop#13](https://github.com/OpenAEC-Foundation/openaec-cloud-desktop/issues/13).
- Model: `UploadServer.tla`, run `UploadServer_pr9_voltooi_eenmalig`; in de sync: `Sync_5b_nietifc_a3`.

### Fout 4. Een herhaald "klaar" kan de inhoud van een vastgelegde versie wissen

Anna's client stuurt "klaar" voor een upload. De server legt de nieuwe versie vast. Net daarna, vóór de server het
uploadbewijs (het token) heeft gewist, valt de worker uit. Anna's client krijgt geen antwoord en stuurt "klaar"
opnieuw, met hetzelfde token. Op S3-opslag die een tweede "klaar" ook als gelukt meldt (Ceph RGW doet dat; AWS S3 en
MinIO geven een fout), loopt de server de upload nog eens af. Nu is Anna's eigen versie de huidige, dus haar basis is
verouderd. Bij "weiger" ruimt de server de upload op en wist hij het S3-object: dat is de inhoud van de versie die al
vastligt. Bij "conflictkopie" wijzen twee versies naar hetzelfde object.

- Waar: `_rond_upload_af` en `_ruim_upload_op` in storage-PR #9 (r. 674-701): het token gaat pas na de commit weg, en
  opruimen kijkt niet of een versie het object al gebruikt.
- De desktop-sync stuurt "klaar" niet twee keer met hetzelfde token. Andere clients mogen dat wel; het commentaar bij
  `een_afronding` gaat daarvan uit.
- Fix: bij een tweede "klaar" eerst kijken of een versie dit S3-object al heeft, en dan die versie teruggeven. En:
  ruim nooit een sleutel op die een versieregel heeft. Dat is ook "optie B" in `offline.md` van desktop-PR #11: een
  herhaalbare afronding, zodat de desktop na een netwerkfout "klaar" gewoon kan herhalen. Zonder deze fix zou een
  desktop die dat doet, deze fout raken.
- Gemeld: [opmerking bij storage-PR #9](https://github.com/OpenAEC-Foundation/openaec-storage/pull/9#issuecomment-6661893101).
- Model: `UploadServer.tla`, runs `UploadServer_pr9_inhoud` en `UploadServer_pr9_lus_voltooi_herhaalbaar`.

### Kleinere bevinding (VERVOLG)

**Een conflictkopie van een nieuw bestand gaat de volgende ronde naar de prullenbak** (5a en 5b). Anna en Piet maken
tegelijk een nieuw `notitie.ifc`. Anna's upload is eerst; Piets upload wordt een conflictkopie. Piets client onthoudt
die kopie in de toestand, maar zet haar niet lokaal neer (zijn eigen bestand blijft op zijn plek). De volgende ronde
ziet de client: de kopie staat in de toestand, op de server, maar niet lokaal. Hij verwijdert haar op de server. In
dezelfde ronde wordt Piets bestand nog eens geüpload, als tweede conflictkopie, en die komt wel lokaal. Er gaat niets
verloren, maar er staat een kopie in de prullenbak die niemand weggooide. Waar: 5b `motor.rs` `na_upload`, de tak
zonder serverbestand (r. 746-750), en `onthoud_als`. Op main faalt hier `onthoud`; dan komt de kopie de volgende ronde
gewoon lokaal. Gemeld: [openaec-cloud-desktop#15](https://github.com/OpenAEC-Foundation/openaec-cloud-desktop/issues/15).

### Bekende fouten, bevestigd

- **De wijzigingenlijst op main slaat regels over.** Schrijver 1 voegt een wijziging toe, maar zijn transactie loopt
  nog. Schrijver 2 voegt er een toe en is eerder klaar. Een client leest de lijst, krijgt de wijziging van schrijver
  2 en zet zijn cursor daarop. Dan is schrijver 1 klaar. Zijn wijziging heeft een lager nummer dan de cursor; de
  client krijgt haar nooit. Storage-PR #16 lost dit op (zie "Wat klopt").
- **Main: twee actieve bestanden met dezelfde naam.** Twee uploads lezen allebei "hier staat nog niets" en maken
  allebei een bestand. Storage-PR #9 lost het op. Een stille overschrijving op een oude basis kan op main alleen onder
  READ COMMITTED; onder de standaard (REPEATABLE READ) geeft Frappe dan een fout (`TimestampMismatch`).
- **Main-desktop: een download overschrijft een net opgeslagen bestand, het kenmerk wordt na de upload gemeten, en
  een lokale verwijdering controleert niet opnieuw** (main `lib.rs` r. 430, `motor.rs` r. 572 en r. 658). Desktop-PR
  #8 lost alle drie op.

## Samenvoegen van IFC

De bibliotheek voegt samen per "register": per object, per eigenschap, per lid van een verzameling. Er zijn drie
soorten registers, elk met een eigen regel:

- **Gewone registers** (het meeste): wie iets veranderde, wint; veranderden beide het anders, dan is het een
  conflict. Voor die regel geldt: geen wijziging valt stil weg, de volgorde van ons en hun maakt niet uit, en nog eens
  samenvoegen verandert niets. De opslaan-lus geeft hetzelfde als in één keer, of is strenger.
- **Aantallen in een BAG**: de aantallen worden opgeteld. Hier geeft de opslaan-lus soms een ander aantal dan in één
  keer samenvoegen (één kopie van een element meer of minder). Er gaat geen element verloren.
- **Vluchtige velden** (tijdstempels, HEADER): veranderden beide kanten, dan wint ons, zonder conflict. Zo bedoeld.

Of dat ook voor echte IFC-bestanden geldt, hangt af van drie voorwaarden: hetzelfde ding moet in elke versie
hetzelfde register krijgen, het geschreven bestand moet zijn wat het plan zegt, en de STEP-nummering mag niet
meetellen. Die voorwaarden zijn niet bewezen; property-based tests toetsen ze, in draft-PR
[openaec-cloud-desktop#17](https://github.com/OpenAEC-Foundation/openaec-cloud-desktop/pull/17) op de branch van
desktop-PR #7.

De tests maken uit vier echte bestanden (IFC2X3, IFC4, IFC4X3, ook een planning van Open Planner Studio) willekeurige
bewerkingen aan beide kanten, en vergelijken de samenvoeging met de bewerkingen na elkaar. Twaalf eigenschappen
houden, waaronder: geen stil verlies, symmetrie, stabiel bij opnieuw samenvoegen, de volgorde van drie clients maakt
niet uit, en hernummeren doet niet mee. Twee houden niet, en allebei gaat het om de tijdstempel:

- **Een conflict te veel.** Slaat iemand dezelfde versie nog eens op na een gelukte samenvoeging (bijvoorbeeld na een
  verloren antwoord), dan komt er een conflict op de tijdstempel in plaats van "niets nieuws". Het werk blijft in de
  conflictkopie.
- **Een tijdstempel valt stil weg.** In een bepaald geval houdt een nieuw object de oude tijdstempel. Dat is geen
  werk, maar wel een afwijking van de regel in het ontwerp.

Om te zien of de tests iets toetsen, zijn twaalf bekende fouten één voor één teruggezet in de bibliotheek. Elke fout
maakte de juiste test rood.

## Aannames: wat het model niet ziet

- **Grenzen.** 2 of 3 clients, 1 of 2 bestanden, een paar bewerkingen, hoogstens 1 netwerkstoring en 1 verwijdering
  per run, en een beperkt aantal conflictkopieën.
- **Inhoud als lijst van bewerkingen.** Elke keer opslaan voegt een bewerking toe. Zo is "verlies" eenvoudig te
  zien: een bewerking die nergens meer staat.
- **Samenvoegen is een vereniging of een conflict.** Dat klopt voor gewone registers, niet precies voor vluchtige
  velden en aantallen (zie hierboven).
- **Het kenmerk is de inhoud.** De echte client vergelijkt grootte en wijzigingstijd. Twee versies met dezelfde
  grootte en tijd ziet de echte client als gelijk; het model niet.
- **Een upload leest het bestand in één keer.** De echte client leest eerst de hashes en later de delen. Opslaan
  daartussen is niet nagegaan.
- **De client crasht niet** midden in een ronde. Het netwerk wel.
- **Geen hernoemen of verplaatsen.** Elk pad staat los.
- **Conflictkopieën** synct in het model alleen de client die ze maakte; de anderen halen ze in het echt alleen op.
- **De database werkt zoals MariaDB** bij REPEATABLE READ (en apart nagegaan: READ COMMITTED en
  `innodb_snapshot_isolation`). S3 zoals beschreven bij fout 4.
- **Een programma dat het bestand open houdt** (en bij opslaan zijn oude kopie terugschrijft) staat standaard uit.
  Het ontwerp noemt dat al een grens die blijft. Een programma dat via een open bestand in de bron schrijft, is apart
  nagegaan (`OpenBestand`); de controle van de client op open processen niet.

## De runs

| Model | Toestanden | Uitkomst |
|---|---|---|
| Wijzigingenlijst, main | 6.648 | fout: slaat over |
| Wijzigingenlijst, PR #16 | 193.723 en 643.565 | geen fout, ook levend |
| Upload, main | 312 | twee bestanden met dezelfde naam |
| Upload, PR #9, opslaan-lus | 42.920 | geen fout |
| Upload, PR #9, alleen nummer | 5.449 | fout 3 |
| Upload, PR #9, herhaald klaar | 65-1.722 | fout 4 |
| Uitchecken, nu en met fix | volgt | |
| Sync, main / 5a / 5b | volgt | |

Een fout wordt meestal snel gevonden; dan stopt TLC. Daarom zijn die getallen klein. Alle runs en logboeken staan in
[`formeel/runs/`](formeel/runs/).

## Open vragen

(volgt)
