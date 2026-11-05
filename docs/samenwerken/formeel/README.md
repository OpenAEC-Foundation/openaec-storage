# Formeel model van het sync-protocol (TLA+)

Stand: 5 november 2026 (na review ronde 1 door Sol). Het rapport in gewone taal staat in [`../formeel-model.md`](../formeel-model.md). Dit
bestand is de technische kant: welke spec wat nabootst, met welke bron, welke aannames, en hoe je de runs herhaalt.

## Draaien

Nodig: Java 17+ en `tla2tools.jar` (TLC). Op aia staan ze in `~/opt` (`jdk-21.0.13+9`, `tla2tools.jar` van
de release `v1.8.0`, build 2026-11-03).

```
./tlc.sh MCWijzigingenlijst Wijzigingenlijst_pr16_3x1.cfg     # één model; log in runs/<cfg>.log
./draai_reeks.sh MCSyncClient Sync_5b_k2p1_fix Sync_5a_k2p1     # meer modellen na elkaar
./maak_sync_cfgs.sh                                            # maakt de Sync_*.cfg opnieuw
```

`tlc.sh` begrenst geheugen (`TLC_XMX`, standaard 3g) en tijd (`TLC_TIJD`, standaard 2 uur) en draait met
`nice`. Op aia gaat elke run via de rij voor zware runs (`ZWAAR`, standaard `/home/aia09/worktrees/zwaar.sh`; zet
`ZWAAR=` om zonder rij te draaien). `draai_reeks.sh` start pas als er geen andere TLC draait, en schrijft een regel
per model in `runs/samenvatting.txt`.

## De vier specs

| Spec | Wat | Bronnen |
|---|---|---|
| `Wijzigingenlijst.tla` | De wijzigingenlijst en de cursor van `wijzigingen()`: schrijvers die regels invoegen en in willekeurige volgorde committen of terugdraaien (ook naar een savepoint), een veger, lezers met een cursor, paginering, twee ruimtes, een lezer via een deling | main `api.py` `wijzigingen()`; PR #16 `commitnummer.py` `veeg()` en `api.py` `wijzigingen()`; ontwerp §5.4 |
| `Slot.tla` | Uitchecken van één bestand: `vergrendel`, `ontgrendel`, `forceer_ontgrendel`, verlopen, en een upload van een ander (`_registreer`) | PR #9 `api.py` `vergrendel` (r. 1455), `ontgrendel` (r. 1482), `_registreer` (r. 711-792), `opslagvergrendeling.json` |
| `UploadServer.tla` | De server bij een upload: tokens, `upload_klaar` (ook twee keer met hetzelfde token, en een uitval tussen commit en het wissen van het token), `_registreer` volgens main en volgens PR #9, verwijderen en opnieuw aanmaken op dezelfde plek | main `api.py` `upload_klaar`, `_registreer`; PR #9 `api.py` r. 471-831 (`upload_start`, `upload_klaar`, `een_afronding`, `_rond_upload_af`, `_registreer`, `_vergelijk_basis`) |
| `SyncClient.tla` | De desktop-sync: per client en pad een ronde (lijst, scan, beslissen, taak, bronnen afronden, toestand bewaren), gebruikers die opslaan en verwijderen, een netwerk dat verzoeken en antwoorden kwijtraakt, conflictkopieën als eigen paden, en de opslaan-lus | openaec-cloud-desktop main `a3aceac` `crates/openaec-sync/src/motor.rs` (`na_upload` r. 611-640, `onthoud` r. 572, r. 658-669, 1008-1013), `lib.rs` r. 430; PR #8 (5a, `f0d144c`) `motor.rs` `na_upload`, `haal_op`, `rond_af`, `lever_in` (r. 727-800), `vervang.rs` `vervang_binnen`, `rond_af`, `rond_verwijdering_af`; desktop-PR #9 (5b, `47eba36`) `motor.rs` r. 728-790, 896-1058, 1590-1735, `opslag.rs` r. 392 (download op `versie_id`), `lib.rs` `vastgelegd`; PR #7 `samenwerken.rs` `sla_op` |

`MC*.tla` geven de modelwaarden (namen van clients, paden, bestanden). Elke `*.cfg` is één model.

## Aannames en vereenvoudigingen

Algemeen:

- **Atomaire stappen.** Elke actie in een spec is één ondeelbare stap. Waar de code meer stappen heeft die elkaar
  kunnen kruisen, zijn ze apart gemodelleerd (lezen en schrijven van `vergrendel`; lijst, scan en de stappen van een
  taak in de sync). Waar een databaseslot stappen al ondeelbaar maakt, is het één stap.
- **Eindige grenzen.** Elke run heeft vaste grenzen (aantal clients, paden, bewerkingen, storingen). TLC loopt alle
  toestanden binnen die grenzen af. Een fout die alleen bij grotere aantallen optreedt, ziet TLC niet.

`Wijzigingenlijst.tla`:

- Een commit maakt alle regels van een transactie tegelijk zichtbaar. `name` komt uit een sequence zonder cache.
- Vegers wachten op elkaar via het slot op de tellerregel; hun momentopname begint na dat slot (MariaDB maakt de
  momentopname bij de eerste gewone lezing, niet bij een `SELECT … FOR UPDATE`). Daarom is één veging één stap.
- Een lezing (één `SELECT`, of twee in dezelfde momentopname) is één stap.

`Slot.tla`:

- `RR`: een gewone lezing komt uit de momentopname van het begin van het verzoek; `DELETE` en `INSERT` werken op de
  laatste vastgelegde stand. `RR_SI`: met `innodb_snapshot_isolation=ON` geeft zo'n `DELETE` fout 1020 (standaard aan
  vanaf MariaDB 11.6.2, ook naar oudere reeksen teruggebracht; zie de
  [systeemvariabele](https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-system-variables#innodb_snapshot_isolation)).
  `RC`: elke lezing ziet de laatste stand. Welke instelling de productieserver heeft, is niet nagegaan.
- `houdt` (en daarmee `EenHouder`) zegt: de client kreeg "vergrendeld" en zijn slot eindigde niet rechtmatig (zelf
  ontgrendeld, verlopen, door een beheerder). Of de client dat einde al hoorde, zit niet in het model.
- De fix is getoetst tegen vergrendelen, ontgrendelen, verlopen, forceren en uploaden. Andere wijzigingen van een
  bestand (verwijderen, verplaatsen) zijn niet nagegaan.
- `DELETE` + `INSERT` + commit van `vergrendel` is één stap. Twee gelijktijdige `DELETE`s onder RR geven een deadlock;
  dat levert alleen een fout op en is weggelaten.
- `_registreer` loopt onder READ COMMITTED (`_eigen_transactie`). Een `SELECT … FOR UPDATE` die geen regel vindt, zet
  onder READ COMMITTED geen gap lock.

`UploadServer.tla`:

- Onder PR #9 is `_registreer` één stap: de sloten op de bestandsregel en op de plek maken hem ondeelbaar tegenover
  andere afrondingen.
- `VoltooiHerhaalbaar`: geeft S3 bij een tweede `complete_multipart_upload` met dezelfde UploadId "gelukt"? Ceph RGW
  wel: hij geeft succes terug voor een al voltooide upload
  ([rgw_op.cc](https://github.com/ceph/ceph/blob/main/src/rgw/rgw_op.cc#L7309)). AWS S3 documenteert `NoSuchUpload`
  voor een al voltooide upload ([API_CompleteMultipartUpload](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CompleteMultipartUpload.html)).
  MinIO controleert het upload-id en geeft de fout door
  ([erasure-multipart.go](https://github.com/minio/minio/blob/master/cmd/erasure-multipart.go#L1045)). De code
  gebruikt standaard AWS; een ander endpoint kan. Bij hergebruikte delen (`kopieer_deel`) kan een herhaling al eerder
  stuklopen; het tegenvoorbeeld gaat over een upload zonder hergebruikte delen.
- Het main-model past `VoltooiHerhaalbaar` niet toe op `KlaarLees`/`KlaarSchrijf`: een herhaald "klaar" is op main
  niet nagegaan. Onder REPEATABLE READ vergelijkt Frappe bij opslaan de wijzigingstijd van een vergrendeld geladen
  document (`TimestampMismatch`); een stille overschrijving op een oude basis is daar dus een fout, geen stille
  overschrijving. Onder READ COMMITTED kan de basisvergelijking oud zijn terwijl het document vers geladen wordt.
- Een client mag `upload_klaar` met hetzelfde token opnieuw sturen. De desktop-sync doet dat niet (een nieuwe poging
  begint met een nieuwe `upload_start`); het commentaar bij `een_afronding` gaat ervan uit dat andere clients het wel
  doen.

`SyncClient.tla`:

- **Inhoud als verzameling bewerkingen (een beperkte abstractie).** Elke opslag door een gebruiker voegt een nieuw atoom toe. Een gebruiker haalt
  nooit een atoom weg, behalve door het bestand bewust te verwijderen (die atomen gaan naar `gewist`). Zo is "verlies"
  eenvoudig: een atoom dat nergens meer staat.
- **Samenvoegen** zonder conflict geeft de vereniging; samenvoegen mag ook altijd een conflict geven (vrije keuze van
  TLC). Dat volgt uit E4 in [`samenvoegen-bewijs.md`](samenvoegen-bewijs.md) voor gewone registers; voor vluchtige
  velden en aantallen in een BAG geldt het niet precies.
- **Kenmerk = inhoud.** De code vergelijkt grootte en wijzigingstijd. Hier is het kenmerk de inhoud zelf; twee
  verschillende inhouden met dezelfde grootte en tijd komen niet voor.
- **Upload lezen is één moment.** De code leest eerst kenmerk en blokhashes en later de delen uit het levende bestand.
  Een opslag van de gebruiker tussen die twee is niet nagegaan.
- **De server is atomair**, met de regels van de code: een gewone upload vergelijkt het nummer (`basis_versie`);
  `schrijf_als` (5b) vergelijkt de versieregel; `verwijder(bestand)` heeft geen basis. De binnenkant staat in
  `UploadServer.tla`.
- **Een ronde per client en pad.** Paden zijn onafhankelijk (geen verplaatsen of hernoemen). De code doet alle paden in
  één ronde, met de verwijderingen aan het eind; dat maakt het venster tussen lijst en verwijderen in het echt groter,
  niet kleiner.
- **Conflictkopieën zijn eigen paden** (`KopiePaden`), elk één keer gebruikt. Alleen de client die de kopie maakte,
  synct dat pad; de andere clients laten het liggen (in het echt halen ze het alleen op). Gebruikers werken alleen op
  `Paden`. Zijn er geen vrije kopiepaden of bestand-ids meer, dan kan die upload in het model niet: dat is een grens
  van de run, net als het aantal atomen.
- **De veilige routine** (5a/5b, `vervang_binnen`) verplaatst een bestand alleen naar de bron als de toestand het kent
  en het kenmerk klopt; anders blijft het staan. `lever_in` zet een bron alleen terug bij een conflict of een
  verwijdering, en alleen op een vrij pad.
- **Open bestanden** (`OpenBestand`): een programma dat het bestand open had, schrijft na de verplaatsing nog in de
  bron (Linux en macOS). De controle op processen (`in_gebruik`) zit niet in het model: een schrijfactie is één stap.
- **Download**: main en 5a halen de nieuwste inhoud van het bestand en onthouden het versienummer uit de lijst; 5b haalt
  precies de versie uit de lijst.
- **Herstel na een verloren antwoord** (5b, `vastgelegd`): alleen bij een gewone upload. Bij `lever_in` en
  `schrijf_als` niet; dan levert een volgende ronde de inhoud nog eens in (hoogstens een kopie te veel).
- **Eén bron per pad.** Ligt er nog een bron, dan wacht een nieuwe vervanging tot een volgende ronde.
- **Geen uitval van de client zelf** (een crash midden in een ronde). Het journaal van 5a is daarom niet nagebootst;
  PR #8 heeft daar eigen proeven voor.
- **Geen sloten in de sync.** Die staan in `Slot.tla`.
- **AppBuffer** (standaard uit): een programma dat het bestand open houdt en bij opslaan zijn eigen kopie terugschrijft.
  Het ontwerp noemt dit een grens die blijft (§5.6, laatste alinea).

## Eigenschappen

| Naam | Spec | Betekenis |
|---|---|---|
| `GeenOverslaan` | Wijzigingenlijst | Een vastgelegde regel die een lezer mag krijgen en nog niet kreeg, ligt nog boven zijn cursor (of heeft nog geen nummer) |
| `CursorOnderTeller` | Wijzigingenlijst | Geen cursor boven de teller: een regel die later een nummer krijgt, komt boven elke cursor |
| `GeenDubbel`, `NummersUniek` | Wijzigingenlijst | Geen regel twee keer; een nummer wijst één regel aan |
| `KrijgtAlles` | Wijzigingenlijst | (levendheid) Een lezer die blijft lezen, krijgt elke vastgelegde regel die voor hem is |
| `EenSlotInDatabase` | Slot | Hoogstens één geldig slot in de database |
| `GeenDiefstal` | Slot | Een geldig slot verdwijnt alleen door de houder, door verlopen of door een beheerder |
| `EenHouder` | Slot | Nooit twee clients die allebei "vergrendeld" kregen terwijl geen van beide sloten rechtmatig eindigde |
| `SlotBeschermt` | Slot | Terwijl iemand een geldig slot heeft, legt een ander geen nieuwe versie vast |
| `InhoudBlijft` | UploadServer | Geen versieregel wijst naar een S3-object dat de server zelf wiste |
| `SleutelEenmaal` | UploadServer | Eén S3-object hoort bij hoogstens één versieregel |
| `GeenStilleOverschrijving` | UploadServer | Een nieuwe versie (geen conflictkopie) is gemaakt op de versie die toen de huidige was |
| `EenActief` | UploadServer | Nooit twee actieve bestanden met dezelfde naam op één plek |
| `GeenVernietiging` | SyncClient | Elke bewerking staat ergens: op de server (ook oud of in de prullenbak), lokaal, of de gebruiker wiste haar zelf |
| `GeenStilVerlies` | SyncClient | Elke bewerking staat in een huidige versie (ook van een conflictkopie) of lokaal (ook in een bron of `.openaec/eigen`), of de gebruiker wiste haar zelf |
| `GeenLostUpdate` | SyncClient | Een nieuwe versie bevat alles van de vorige versie, behalve wat een gebruiker bewust wiste |
| `Convergentie` | SyncClient | (levendheid) Als niemand meer iets doet en het netwerk werkt, komen alle clients op de stand van de server, en staat elke bewerking op de server |

## Uitkomsten

Zie [`runs/samenvatting.txt`](runs/samenvatting.txt) en de logboeken in `runs/`. Het rapport zet ze op een rij, met
de tegenvoorbeelden als verhaal.
