"""Het commitnummer van de wijzigingenlijst (ontwerp samenwerken §5.4, K16).

Frappe geeft een Opslagwijziging haar `name` bij het invoegen (een sequence), dus in de volgorde van invoegen en niet
in die van committen. Voorbeeld: transactie A krijgt 41 en blijft open, B krijgt 42 en commit. Een client leest 42 en
bewaart cursor 42; daarna commit A, en `name > 42` laat 41 nooit meer zien.

Daarom krijgt elke regel vlak vóór de commit een commitnummer. De transactie vergrendelt de tellerregel, geeft haar
eigen nieuwe regels oplopende nummers, zet de teller bij en commit. De teller blijft vergrendeld tot de commit klaar
is: wie het volgende nummer wil, wacht tot het vorige gecommit is. Zo committen de nummers in volgorde, en verschijnt
er na een gelezen nummer N nooit meer een regel met een nummer van N of lager. Een teruggedraaide transactie deelt
geen nummer uit, dus een rollback laat geen gat. Een gat binnen één ruimte is gewoon een regel van een andere ruimte.

Hoe het loopt:
- Elke insert van een Opslagwijziging (de haak `after_insert` op het doctype, dus elke weg: verzoek, taak, patch)
  zet de naam op de lijst van deze verbinding en meldt `_nummer` aan bij `frappe.db.before_commit`, als hij daar nog
  niet staat.
- Frappe draait een before_commit-callback per commit één keer en haalt hem dan weg. Na een commit meldt de volgende
  insert de nummeraar dus opnieuw aan; een taak die tussendoor commit, nummert zo bij elke commit zijn eigen regels.
  Een volledige rollback haalt de callback ook weg; de namen op de lijst bestaan dan niet meer.
- Een rollback naar een savepoint laat de callback staan, maar haalt regels weg. De nummeraar nummert daarom alleen
  regels van de lijst die nog bestaan en nog geen nummer hebben.
- Staan er bij de commit nog andere before_commit-callbacks na de nummeraar, dan zet hij zichzelf achteraan en laat
  die eerst draaien. Zo is het slot op de teller altijd het laatste slot van de transactie (eerst bestand of map, dan
  de teller; geen kring van wachtende transacties), en krijgt een regel die zo'n callback nog schrijft, gewoon een
  nummer in dezelfde commit. Schrijft een callback toch nog een regel nadat de nummeraar klaar is (alleen na
  `MAX_UITSTEL` keer achteraan zetten), dan meldt de insert hem opnieuw aan en nummert hij in dezelfde commit door. Dat
  is veilig: deze transactie houdt de teller dan nog vast, dus geen andere transactie kan er een nummer tussen zetten.

In de tests commit de hoofdthread nooit (elke test eindigt met een rollback). Daar nummert de insert de regel meteen,
zodat een test in dezelfde transactie de wijzigingenlijst kan lezen. Een test die het echte gedrag nodig heeft, zet
`frappe.flags.oa_nummer_bij_commit`; andere threads (een tweede verbinding in een test) werken altijd zoals buiten de
tests."""

import threading
from contextlib import contextmanager

import frappe
from frappe.utils import cint

TELLER = "openaec_storage.commitnummer"  # de tellerregel in tabSeries
MAX_UITSTEL = 20  # zo vaak zet de nummeraar zich hoogstens achter andere callbacks (tegen een eindeloze wissel)


def meld(naam) -> None:
	"""Haak na het invoegen van een Opslagwijziging: de regel krijgt bij de commit een nummer."""
	staat = _staat()
	staat["namen"].append(naam)
	if _direct():
		deel_uit()
	elif _nummer not in frappe.db.before_commit._functions:
		staat["uitstel"] = 0
		frappe.db.before_commit.add(_nummer)


def _staat() -> dict:
	"""Wat deze databaseverbinding bijhoudt. Het hoort bij de verbinding, net als de callbacks: een nieuwe verbinding
	begint leeg."""
	staat = getattr(frappe.db, "_oa_commitnummer", None)
	if staat is None:
		staat = frappe.db._oa_commitnummer = {"namen": [], "uitstel": 0}
	return staat


def _direct() -> bool:
	return (
		frappe.in_test
		and threading.current_thread() is threading.main_thread()
		and not frappe.flags.oa_nummer_bij_commit
	)


def _nummer() -> None:
	"""De before_commit-callback."""
	staat = _staat()
	if frappe.db.before_commit._functions and staat["uitstel"] < MAX_UITSTEL:
		staat["uitstel"] += 1
		frappe.db.before_commit.add(_nummer)
		return
	staat["uitstel"] = 0
	deel_uit()


def deel_uit() -> None:
	"""Geef de eigen regels zonder nummer oplopende nummers, in `name`-volgorde, onder het slot op de teller."""
	staat = _staat()
	namen, staat["namen"] = staat["namen"], []
	if not namen:
		return
	eigen = frappe.db.sql_list(
		"select name from `tabOpslagwijziging` where name in %(namen)s and commitnummer = 0 order by name",
		{"namen": tuple(namen)},
	)
	if not eigen:
		return
	with _laatste_stand():
		teller = _vergrendel_teller()
		frappe.db.sql("set @oa_commitnummer := %s", teller)
		frappe.db.sql(
			"""update `tabOpslagwijziging` set commitnummer = (@oa_commitnummer := @oa_commitnummer + 1)
			where name in %(eigen)s and commitnummer = 0 order by name""",
			{"eigen": tuple(eigen)},
		)
		frappe.db.sql("update `tabSeries` set current = @oa_commitnummer where name = %s", TELLER)


def _vergrendel_teller() -> int:
	"""Het slot op de teller; het duurt tot de commit klaar is."""
	rij = frappe.db.sql("select current from `tabSeries` where name = %s for update", TELLER)
	if not rij:
		zorg_voor_teller()
		rij = frappe.db.sql("select current from `tabSeries` where name = %s for update", TELLER)
	return cint(rij[0][0])


@contextmanager
def _laatste_stand():
	"""MariaDB met `innodb_snapshot_isolation=ON` (standaard sinds 11.8) weigert een vergrendelende lees- of
	schrijfactie op een regel die na het begin van de momentopname van deze transactie gecommit is (fout 1020, in
	Frappe een QueryDeadlockError). De teller verandert bij elke commit, en hier moet juist de laatste gecommitte
	stand gelden. Daarom staat die controle tijdens het uitdelen uit, alleen voor deze sessie, en daarna weer aan."""
	aan = False
	if frappe.db.db_type == "mariadb":
		try:
			aan = bool(cint(frappe.db.sql("select @@session.innodb_snapshot_isolation")[0][0]))
		except Exception:
			aan = False  # een versie zonder deze instelling
	if aan:
		frappe.db.sql("set session innodb_snapshot_isolation = OFF")
	try:
		yield
	finally:
		if aan:
			frappe.db.sql("set session innodb_snapshot_isolation = ON")


def zorg_voor_teller() -> None:
	"""De tellerregel, en nooit lager dan het hoogste `name`. Een commitnummer is nooit groter dan de `name` van zijn
	regel (de teller begint op het hoogste `name` en loopt alleen op voor gecommitte regels), dus zo valt een nieuw
	nummer nooit onder een bestaand nummer of onder een cursor die een client al bewaart."""
	hoogste = cint(frappe.db.sql("select coalesce(max(name), 0) from `tabOpslagwijziging`")[0][0])
	frappe.db.sql(
		"""insert into `tabSeries` (name, current) values (%(teller)s, %(hoogste)s)
		on duplicate key update current = greatest(current, %(hoogste)s)""",
		{"teller": TELLER, "hoogste": hoogste},
	)
