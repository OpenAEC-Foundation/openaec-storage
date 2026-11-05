# Bekende fouten en vervolgpunten

Punten die bewust niet in de PR zijn opgelost. Een nummer wordt nooit hergebruikt. Zekerheid: `B` = bevestigd in de
code of met een proef, `V` = vermoeden.

1. **De veger ziet schrijfwerk van vóór een teruggedraaid savepoint niet** (PR #16, zekerheid B). De veger weigert een
   transactie met schrijfwerk via `frappe.db.transaction_writes`. Frappe zet die teller op 0 bij elke `rollback`,
   ook bij `rollback to savepoint` (`frappe/database/database.py`, `check_transaction_status`). Schrijft code iets,
   draait ze daarna terug naar een savepoint en leest ze dan `wijzigingen()`, dan commit de veger het eerdere
   schrijfwerk. Geen lezer in de app doet dat; het is een vangnet voor een programmeerfout. Een zekere controle
   (`information_schema.innodb_trx`) vraagt het recht PROCESS, dat de site-gebruiker niet heeft.
2. **Het antwoord van `wijzigingen` heeft een extra sleutel `commitnummer`** (PR #16, zekerheid B). In
   `{**r, "volgnummer": cint(r.pop("commitnummer"))}` komt `**r` eerst, dus de sleutel blijft staan, met dezelfde
   waarde als `volgnummer`. Op main was dat `name` (zelfde patroon). De README noemt alleen `volgnummer`; de
   desktop-sync negeert onbekende sleutels. Opruimen: pop vóór het uitpakken.
3. **Een schrijver kan een bestaande wijzigingsregel opslaan** (bestond al vóór PR #16; zekerheid B voor `doc.save()`
   als schrijver, zie `TestNummerVanBuiten`; V voor `/api/resource` en voor de gevolgen). `heeft_recht` geeft voor
   dit doctype het recht van de ruimte, dus een schrijver kan regels invoegen, wijzigen en verwijderen. Het
   commitnummer is beschermd, de andere velden niet. `validate` houdt het commitnummer gelijk aan de waarde in de
   database. Zo'n verzoek houdt een rijslot vast waar een veger kort op wacht. Met `innodb_snapshot_isolation` uit
   (standaard vóór MariaDB 11.8) leest `validate` een oude momentopname en kan het opslaan het nummer terugzetten op 0;
   de regel krijgt dan een tweede nummer en een client ziet haar twee keer. Oplossing: een wijzigingsregel
   onveranderlijk maken na het invoegen.
