"""Het commitnummer van de wijzigingenlijst (ontwerp samenwerken §5.4, stap 4b).

Elke `Verbinding` is een eigen thread met een eigen Frappe-context en databaseverbinding, zoals een verzoek of taak op
een tweede werker. De test bepaalt stap voor stap wie invoegt, wie commit en wie terugdraait; wat niet gecommit is,
blijft open staan tot de volgende stap. Wat een andere verbinding moet zien, is echt gecommit, dus deze tests ruimen
hun gegevens zelf op. De hoofdthread leest alleen, telkens met een verse momentopname."""

import queue
import random
import threading
import time
from unittest import mock

import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage import api, commitnummer, ruimtebeheer
from openaec_storage.patches import commitnummer_invullen
from openaec_storage.tests.hulp import gebruiker, ruim_op, ruimte

WACHT = 60  # seconden: hoogstens zo lang wachten op een stap in een andere verbinding


class Verbinding(threading.Thread):
	"""Een eigen databaseverbinding in een eigen thread. `doe(functie)` voert de functie daar uit en geeft het
	resultaat (of gooit de fout); `begin(functie)` start hem zonder te wachten en geeft een wachtrij voor de uitkomst."""

	def __init__(self, test, naam: str):
		super().__init__(name=naam, daemon=True)
		self.site, self.sites_path = test.site, test.sites_path
		self.opdrachten = queue.Queue()

	def run(self):
		frappe.init(site=self.site, sites_path=self.sites_path)
		try:
			frappe.connect()
			frappe.set_user("Administrator")
			while (opdracht := self.opdrachten.get()) is not None:
				functie, uitkomst = opdracht
				try:
					uitkomst.put((True, functie()))
				except Exception as fout:
					uitkomst.put((False, fout))
		finally:
			try:
				frappe.db.rollback()
			finally:
				frappe.destroy()

	def begin(self, functie) -> queue.Queue:
		uitkomst = queue.Queue()
		self.opdrachten.put((functie, uitkomst))
		return uitkomst

	def doe(self, functie):
		return uitkomst_van(self.begin(functie))

	def stop(self):
		self.opdrachten.put(None)
		self.join(WACHT)


def uitkomst_van(uitkomst: queue.Queue):
	gelukt, waarde = uitkomst.get(timeout=WACHT)
	if not gelukt:
		raise waarde
	return waarde


# Deze functies draaien in de thread van een verbinding (frappe.db is daar de eigen verbinding).


def schrijf(ruimte_naam: str, doel: str, doel_doctype: str = "Opslagbestand") -> int:
	"""Eén regel in de wijzigingenlijst, langs dezelfde weg als elke handeling; geeft de `name`."""
	api._log(ruimte_naam, "eigenschappen", doel_doctype, doel, doel)
	return frappe.db.sql(
		"select max(name) from `tabOpslagwijziging` where doel = %s and soort = 'eigenschappen'", doel
	)[0][0]


def commit():
	frappe.db.commit()


def rollback():
	frappe.db.rollback()


class Basis(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		self.anna = gebruiker("anna")
		self.r1 = ruimte("Project Een", {self.anna: "beheerder"})
		self.r2 = ruimte("Project Twee", {self.anna: "beheerder"})
		self.site, self.sites_path = frappe.local.site, frappe.local.sites_path
		self.verbindingen = []
		frappe.db.commit()

	def tearDown(self):
		for v in self.verbindingen:
			v.stop()
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.db.delete("Opslagdeling", {"ruimte": ("in", [self.r1, self.r2])})
		frappe.db.delete("Notification Log", {"for_user": ("like", "%@storage-test.localhost")})
		ruim_op()
		frappe.db.commit()

	def verbinding(self, naam: str) -> Verbinding:
		v = Verbinding(self, naam)
		v.start()
		self.verbindingen.append(v)
		return v

	# --- lezen in de hoofdthread, telkens met een verse momentopname -------------------------------------

	def lees(self, ruimte_naam: str | None = None, sinds: int = 0, limiet: int = 1000, deling=None, als=None) -> dict:
		frappe.db.rollback()
		frappe.set_user(als or self.anna)
		try:
			return api.wijzigingen(ruimte=ruimte_naam, sinds=sinds, limiet=limiet, deling=deling)
		finally:
			frappe.set_user("Administrator")

	def nummer(self, name: int) -> int | None:
		frappe.db.rollback()
		return frappe.db.get_value("Opslagwijziging", name, "commitnummer")

	def name_van(self, doel: str) -> int | None:
		frappe.db.rollback()
		return frappe.db.get_value("Opslagwijziging", {"doel": doel}, "name")

	def teller(self) -> int:
		frappe.db.rollback()
		return frappe.db.sql("select current from `tabSeries` where name = %s", commitnummer.TELLER)[0][0]


def doelen(antwoord: dict) -> list[str]:
	return [w["doel"] for w in antwoord["wijzigingen"]]


class TestCommitnummer(Basis):
	def test_omgekeerde_commitvolgorde_mist_niets(self):
		a, b = self.verbinding("A"), self.verbinding("B")
		begin = self.lees(self.r1)["cursor"]
		eerst_ingevoegd = a.doe(lambda: schrijf(self.r1, "a.ifc"))
		later_ingevoegd = b.doe(lambda: schrijf(self.r1, "b.ifc"))
		b.doe(commit)
		eerste = self.lees(self.r1, begin)
		a.doe(commit)
		tweede = self.lees(self.r1, eerste["cursor"])
		self.assertEqual(doelen(eerste) + doelen(tweede), ["b.ifc", "a.ifc"])
		# A voegde eerst in (lagere name) maar committe als laatste: A krijgt het hogere nummer.
		self.assertLess(eerst_ingevoegd, later_ingevoegd)
		self.assertGreater(self.nummer(eerst_ingevoegd), self.nummer(later_ingevoegd))

	def test_teruggedraaide_transactie_geeft_geen_gat_en_geen_nummer(self):
		a, b = self.verbinding("A"), self.verbinding("B")
		voor = self.teller()
		begin = self.lees(self.r1)["cursor"]
		b.doe(lambda: schrijf(self.r1, "blijft.ifc"))
		weg = a.doe(lambda: schrijf(self.r1, "weg.ifc"))
		a.doe(rollback)
		b.doe(commit)
		# A meldt de nummeraar na de rollback bij de volgende regel opnieuw aan.
		a.doe(lambda: schrijf(self.r1, "daarna.ifc"))
		a.doe(commit)
		uit = self.lees(self.r1, begin)
		self.assertEqual(doelen(uit), ["blijft.ifc", "daarna.ifc"])
		volgnummers = [w["volgnummer"] for w in uit["wijzigingen"]]
		self.assertEqual(volgnummers[1], volgnummers[0] + 1, "een rollback laat geen gat")
		self.assertIsNone(self.nummer(weg))
		self.assertEqual(volgnummers, [voor + 1, voor + 2])
		self.assertEqual(self.teller(), voor + 2)

	def test_twee_ruimtes_krijgen_elk_hun_eigen_regels_met_unieke_nummers(self):
		a, b = self.verbinding("A"), self.verbinding("B")
		c1, c2 = self.lees(self.r1)["cursor"], self.lees(self.r2)["cursor"]
		namen = {}
		namen["een-1"] = a.doe(lambda: schrijf(self.r1, "een-1"))
		namen["twee-1"] = b.doe(lambda: schrijf(self.r2, "twee-1"))
		b.doe(commit)
		namen["een-2"] = a.doe(lambda: schrijf(self.r1, "een-2"))
		a.doe(commit)
		namen["twee-2"] = b.doe(lambda: schrijf(self.r2, "twee-2"))
		b.doe(commit)
		een, twee = self.lees(self.r1, c1), self.lees(self.r2, c2)
		self.assertEqual(doelen(een), ["een-1", "een-2"])
		self.assertEqual(doelen(twee), ["twee-1", "twee-2"])
		# Over de hele site in commitvolgorde; het gat in de nummers van een ruimte is een regel van de andere.
		volgorde = sorted(namen, key=lambda d: self.nummer(namen[d]))
		self.assertEqual(volgorde, ["twee-1", "een-1", "een-2", "twee-2"])
		nummers = [w["volgnummer"] for w in een["wijzigingen"] + twee["wijzigingen"]]
		self.assertEqual(len(set(nummers)), 4)

	def test_lege_pagina_houdt_de_cursor_en_mist_de_commit_erna_niet(self):
		a, b = self.verbinding("A"), self.verbinding("B")
		begin = self.lees(self.r1)["cursor"]
		a.doe(lambda: schrijf(self.r1, "laat.ifc"))
		b.doe(lambda: schrijf(self.r2, "elders.ifc"))
		b.doe(commit)
		leeg = self.lees(self.r1, begin)
		self.assertEqual((leeg["wijzigingen"], leeg["cursor"], leeg["meer"]), ([], begin, False))
		a.doe(commit)
		self.assertEqual(doelen(self.lees(self.r1, leeg["cursor"])), ["laat.ifc"])

	def test_deling_als_wortel_schuift_de_cursor_niet_voorbij_een_open_regel(self):
		bea = gebruiker("bea")
		frappe.set_user(self.anna)
		gedeeld = api.maak_map(ruimte=self.r1, naam="Gedeeld")["map"]
		binnen = api.maak_map(ruimte=self.r1, naam="Binnen", bovenliggende=gedeeld)["map"]
		buiten = api.maak_map(ruimte=self.r1, naam="Buiten")["map"]
		with mock.patch("frappe.sendmail"):
			deling = api.deel("Opslagmap", gedeeld, soort="gebruiker", gebruiker=bea, rechten="lezen")["deling"]
		frappe.set_user("Administrator")
		frappe.db.commit()

		a, b = self.verbinding("A"), self.verbinding("B")
		begin = self.lees(deling=deling, als=bea)["cursor"]
		a.doe(lambda: schrijf(self.r1, binnen, "Opslagmap"))
		buiten_regel = b.doe(lambda: schrijf(self.r1, buiten, "Opslagmap"))
		b.doe(commit)
		eerste = self.lees(sinds=begin, deling=deling, als=bea)
		a.doe(commit)
		tweede = self.lees(sinds=eerste["cursor"], deling=deling, als=bea)
		self.assertEqual(eerste["wijzigingen"], [])
		self.assertEqual([(w["doel_doctype"], w["doel"]) for w in tweede["wijzigingen"]], [("Opslagmap", binnen)])
		# De eerste pagina had alleen de regel buiten de wortel: na het filter leeg, de cursor is die regel.
		self.assertEqual(eerste["cursor"], self.nummer(buiten_regel))

	def test_volle_pagina_schuift_de_cursor_op(self):
		a = self.verbinding("A")
		begin = self.lees(self.r1)["cursor"]

		def vijf():
			namen = [schrijf(self.r1, f"p{i}") for i in range(5)]
			frappe.db.commit()
			return namen

		namen = a.doe(vijf)
		gezien, paginas, cursor = [], [], begin
		while True:
			uit = self.lees(self.r1, cursor, limiet=2)
			paginas.append((len(uit["wijzigingen"]), uit["meer"]))
			gezien += doelen(uit)
			self.assertGreater(uit["cursor"], cursor)
			cursor = uit["cursor"]
			if not uit["meer"]:
				break
		self.assertEqual(gezien, [f"p{i}" for i in range(5)])
		self.assertEqual(paginas, [(2, True), (2, True), (1, False)])
		self.assertEqual(cursor, self.nummer(namen[-1]))
		leeg = self.lees(self.r1, cursor, limiet=2)
		self.assertEqual((leeg["wijzigingen"], leeg["cursor"], leeg["meer"]), ([], cursor, False))

	def test_taak_die_tussendoor_commit_nummert_bij_elke_commit(self):
		a, b = self.verbinding("taak"), self.verbinding("ander")
		begin = self.lees(self.r1)["cursor"]

		def stap(doel: str, keten: bool = False):
			name = schrijf(self.r1, doel)
			frappe.db.commit(chain=keten)
			return name

		namen = [
			a.doe(lambda: stap("taak-1", keten=True)),
			b.doe(lambda: stap("ander")),
			a.doe(lambda: stap("taak-2")),
			a.doe(lambda: stap("taak-3")),
		]
		nummers = [self.nummer(n) for n in namen]
		self.assertTrue(all(nummers), nummers)
		self.assertEqual(nummers, list(range(nummers[0], nummers[0] + 4)))
		self.assertEqual(doelen(self.lees(self.r1, begin)), ["taak-1", "ander", "taak-2", "taak-3"])

	def test_batch_met_een_item_terug_naar_een_savepoint(self):
		a = self.verbinding("A")
		begin = self.lees(self.r1)["cursor"]

		def batch():
			schrijf(self.r1, "item-1")
			frappe.db.savepoint("item_2")
			weg = schrijf(self.r1, "item-2")
			frappe.db.rollback(save_point="item_2")
			schrijf(self.r1, "item-3")
			frappe.db.commit()
			return weg

		weg = a.doe(batch)
		uit = self.lees(self.r1, begin)
		self.assertEqual(doelen(uit), ["item-1", "item-3"])
		volgnummers = [w["volgnummer"] for w in uit["wijzigingen"]]
		self.assertEqual(volgnummers[1], volgnummers[0] + 1, "het teruggedraaide item laat geen gat")
		self.assertEqual(self.teller(), volgnummers[1])
		self.assertIsNone(self.nummer(weg))

	def test_alles_terug_naar_een_savepoint_neemt_geen_slot_op_de_teller(self):
		a = self.verbinding("A")
		voor = self.teller()
		echte, sloten = commitnummer._vergrendel_teller, []

		def vergrendel():
			sloten.append(1)
			return echte()

		def batch():
			frappe.db.savepoint("alles")
			schrijf(self.r1, "terug")
			frappe.db.rollback(save_point="alles")
			frappe.db.commit()

		with mock.patch.object(commitnummer, "_vergrendel_teller", vergrendel):
			a.doe(batch)
		self.assertEqual((sloten, self.teller()), ([], voor))

	def test_callback_na_de_nummeraar_schrijft_nog_een_regel(self):
		a = self.verbinding("A")
		volgorde = []
		echte = commitnummer._vergrendel_teller

		def vergrendel():
			volgorde.append("teller")
			return echte()

		def callback():
			volgorde.append("callback")
			schrijf(self.r1, "uit-callback")

		def transactie():
			gewoon = schrijf(self.r1, "gewoon")  # meldt de nummeraar aan
			frappe.db.before_commit.add(callback)  # staat ná de nummeraar
			frappe.db.commit()
			return gewoon

		with mock.patch.object(commitnummer, "_vergrendel_teller", vergrendel):
			gewoon = a.doe(transactie)
		# Het slot op de teller komt als laatste, ook na een callback die later is aangemeld.
		self.assertEqual(volgorde, ["callback", "teller"])
		self.assertEqual(self.nummer(self.name_van("uit-callback")), self.nummer(gewoon) + 1)

	def test_callback_die_blijft_uitstellen_breekt_de_commit_af(self):
		"""De proef van Sol (fixronde 1, punt 2): een callback zet zich steeds opnieuw achteraan, en draait als laatste
		handeling terug naar een savepoint van na het maken van de map en vóór de nummering. Vroeger nummerde de
		nummeraar na `MAX_UITSTEL` keer toch: tellerslot, de callback zag het nummer, de rollback naar het savepoint
		maakte het weer 0, en de SQL-commit legde map en regel vast met nummer 0 (de lijst gaf ze nooit). Nu mislukt de
		commit; er komt niets vast. Een nieuwe poging nummert opnieuw: als de callback klaar is, is de nummeraar de
		laatste."""
		a = self.verbinding("A")
		voor = self.teller()
		gemaakt, gezien = {}, []

		def koppig():
			gemaakt["rondes"] += 1
			if gemaakt["rondes"] <= commitnummer.MAX_UITSTEL + 2:
				frappe.db.before_commit.add(koppig)
			else:
				gezien.append(frappe.db.get_value("Opslagwijziging", gemaakt["regel"], "commitnummer"))
				frappe.db.rollback(save_point="na_de_map")

		def transactie():
			gemaakt["map"] = api.maak_map(ruimte=self.r1, naam="Koppig")["map"]
			gemaakt["regel"] = frappe.db.get_value("Opslagwijziging", {"doel": gemaakt["map"]}, "name")
			gemaakt["rondes"] = 0
			frappe.db.savepoint("na_de_map")
			frappe.db.before_commit.add(koppig)
			frappe.db.commit()

		with self.assertRaises(commitnummer.NummeringMislukt):
			a.doe(transactie)
		# Niets vastgelegd: geen map, geen regel (ook niet met nummer 0), en de teller staat stil.
		self.assertIsNone(self.name_van(gemaakt["map"]))
		self.assertFalse(frappe.db.exists("Opslagmap", gemaakt["map"]))
		self.assertEqual(self.teller(), voor)
		self.assertEqual(gezien, [])
		# Een nieuwe poging: de callback draait zijn laatste rondes (en ziet nummer 0), daarna nummert de nummeraar.
		a.doe(commit)
		self.assertEqual(gezien, [0])
		nummer = self.nummer(gemaakt["regel"])
		self.assertEqual(nummer, voor + 1)
		self.assertEqual(self.teller(), nummer)
		self.assertIn(gemaakt["map"], doelen(self.lees(self.r1)))

	def test_wachttijd_op_de_teller_om_dan_mislukt_de_commit_en_nummert_de_volgende(self):
		"""De proef van Sol (fixronde 1, punt 1): een tweede verbinding houdt de teller vast; `maak_map`; met een
		wachttijd van 1 s geeft `commit()` 1205 (QueryTimeoutError). Vroeger had de nummeraar zijn lijst en zijn
		aanmelding dan al weggegooid: de volgende `commit()` legde map en regel vast met nummer 0, en
		`wijzigingen(sinds=0)` gaf ze nooit. Nu staan na de fout de naam nog op de lijst en de nummeraar nog in de
		wachtrij, en nummert de volgende commit."""
		a, b = self.verbinding("A"), self.verbinding("B")
		voor = self.teller()
		b.doe(lambda: frappe.db.sql("select current from `tabSeries` where name = %s for update", commitnummer.TELLER))

		def map_maken():
			frappe.db.sql("set session innodb_lock_wait_timeout = 1")
			map_id = api.maak_map(ruimte=self.r1, naam="Wacht")["map"]
			return map_id, frappe.db.get_value("Opslagwijziging", {"doel": map_id}, "name")

		map_id, regel = a.doe(map_maken)
		with self.assertRaises(frappe.QueryTimeoutError):
			a.doe(commit)
		namen, aangemeld = a.doe(
			lambda: (list(commitnummer._staat()["namen"]), list(frappe.db.before_commit._functions).count(commitnummer._nummer))
		)
		self.assertEqual((namen, aangemeld), ([regel], 1))
		self.assertIsNone(self.name_van(map_id), "niets vastgelegd")
		b.doe(rollback)
		a.doe(commit)
		self.assertEqual(self.nummer(regel), voor + 1)
		self.assertEqual(self.teller(), voor + 1)
		self.assertIn(map_id, doelen(self.lees(self.r1, sinds=0)))
		self.assertEqual(
			frappe.db.sql("select count(*) from `tabOpslagwijziging` where ruimte = %s and commitnummer = 0", self.r1)[0][0], 0
		)

	def test_halve_nummering_gaat_helemaal_terug(self):
		"""Mislukt het uitdelen na het nummeren van de regels maar vóór de teller is bijgezet, dan gaat alles terug naar
		het savepoint van de nummeraar. Anders had de regel al een nummer boven de teller: een nieuwe poging sloeg haar
		over, en een andere transactie kon hetzelfde nummer nog eens uitgeven."""
		a = self.verbinding("A")
		voor = self.teller()

		def transactie():
			regel = schrijf(self.r1, "half")
			echte, keer = frappe.db.sql, {"aantal": 0}

			def sql(query, *args, **kwargs):
				if str(query).startswith("update `tabSeries`") and not keer["aantal"]:
					keer["aantal"] += 1
					raise frappe.QueryTimeoutError("nagebootst")
				return echte(query, *args, **kwargs)

			with mock.patch.object(frappe.db, "sql", sql):
				try:
					frappe.db.commit()
				except frappe.QueryTimeoutError:
					pass
			na_de_fout = frappe.db.get_value("Opslagwijziging", regel, "commitnummer")
			frappe.db.commit()
			return regel, na_de_fout

		regel, na_de_fout = a.doe(transactie)
		self.assertEqual(na_de_fout, 0, "de regel houdt geen nummer van een mislukte nummering")
		self.assertEqual((self.nummer(regel), self.teller()), (voor + 1, voor + 1))

	def test_activiteit_sorteert_en_nummert_op_het_commitnummer(self):
		a, b = self.verbinding("A"), self.verbinding("B")
		a.doe(lambda: schrijf(self.r1, "a.ifc"))
		b.doe(lambda: schrijf(self.r1, "b.ifc"))
		b.doe(commit)
		a.doe(commit)
		frappe.db.rollback()
		frappe.set_user(self.anna)
		try:
			activiteit = ruimtebeheer.activiteit(self.r1, aantal=500)
		finally:
			frappe.set_user("Administrator")
		eigen = [(r["doel"], r["volgnummer"]) for r in activiteit["regels"] if r["doel"] in ("a.ifc", "b.ifc")]
		lijst = self.lees(self.r1)["wijzigingen"]
		uit_lijst = {w["doel"]: w["volgnummer"] for w in lijst}
		# Nieuwste eerst: A committe als laatste. Hetzelfde nummer als in de wijzigingenlijst.
		self.assertEqual([d for d, _ in eigen], ["a.ifc", "b.ifc"])
		self.assertEqual(dict(eigen), {d: uit_lijst[d] for d in ("a.ifc", "b.ifc")})

	def test_gelijktijdig_schrijven_en_lezen_mist_niets(self):
		"""Drie schrijvers met korte transacties (soms tussen invoegen en commit even wachten) en één lezer die
		steeds vanaf zijn cursor leest. De lezer krijgt elke regel precies één keer, in oplopende volgorde."""
		schrijvers = [self.verbinding(f"S{i}") for i in range(3)]
		lezer = self.verbinding("L")
		begin = self.lees(self.r1)["cursor"]

		def schrijf_veel(wie: str) -> list[str]:
			toeval, geschreven = random.Random(wie), []
			for t in range(25):
				for k in range(toeval.randint(1, 3)):
					geschreven.append(f"{wie}-{t}-{k}")
					schrijf(self.r1, geschreven[-1])
				time.sleep(toeval.random() * 0.01)
				frappe.db.commit()
			return geschreven

		def lees_vanaf(cursor: int) -> dict:
			frappe.db.rollback()
			frappe.set_user(self.anna)
			try:
				return api.wijzigingen(ruimte=self.r1, sinds=cursor, limiet=7)
			finally:
				frappe.set_user("Administrator")

		bezig = [s.begin(lambda w=s.name: schrijf_veel(w)) for s in schrijvers]
		gezien, volgnummers, cursor = [], [], begin
		while True:
			# Zijn alle schrijvers al klaar vóór deze lezing, dan ziet ze alles; lees dan door tot de laatste pagina.
			klaar = all(not q.empty() for q in bezig)
			uit = lezer.doe(lambda c=cursor: lees_vanaf(c))
			gezien += doelen(uit)
			volgnummers += [w["volgnummer"] for w in uit["wijzigingen"]]
			cursor = uit["cursor"]
			if klaar and not uit["meer"]:
				break
		geschreven = [d for q in bezig for d in uitkomst_van(q)]
		self.assertEqual(sorted(gezien), sorted(geschreven), "de lezer mist regels of krijgt ze dubbel")
		self.assertEqual(volgnummers, sorted(set(volgnummers)))
		print(f"\n  {self._testMethodName}: {len(geschreven)} regels, lezer zag ze allemaal precies één keer")

	def test_slot_op_de_teller_duurt_kort_ook_bij_een_batch(self):
		"""Meting: hoe lang een transactie het slot op de teller houdt, bij 1, 100 en 500 regels in één transactie.
		Het slot loopt van het vergrendelen tot de SQL-commit klaar is. Wat Frappe daarna nog doet (de after_commit-
		callbacks van de ingevoegde documenten), valt erbuiten; dat staat er apart bij."""
		a = self.verbinding("A")
		echte = commitnummer._vergrendel_teller

		def vergrendel():
			waarde = echte()
			frappe.local.oa_slot_sinds = time.perf_counter()
			return waarde

		def batch(n: int) -> tuple[float, float, float]:
			for i in range(n):
				schrijf(self.r1, f"batch-{n}-{i}")
			momenten, echte_sql = {}, frappe.db.sql

			def sql(query, *args, **kwargs):
				uit = echte_sql(query, *args, **kwargs)
				if str(query).strip() == "commit":
					momenten["commit"] = time.perf_counter()
				return uit

			frappe.db.sql = sql
			try:
				begin = time.perf_counter()
				frappe.db.commit()
				einde = time.perf_counter()
			finally:
				del frappe.db.sql
			return momenten["commit"] - frappe.local.oa_slot_sinds, einde - begin, einde - momenten["commit"]

		metingen = {}
		with mock.patch.object(commitnummer, "_vergrendel_teller", vergrendel):
			for n in (1, 100, 500):
				metingen[n] = a.doe(lambda n=n: batch(n))
		verslag = ", ".join(
			f"{n} regels: slot {s * 1000:.1f} ms (hele commit {c * 1000:.1f} ms, waarvan na de SQL-commit {na * 1000:.1f} ms)"
			for n, (s, c, na) in metingen.items()
		)
		print(f"\n  {self._testMethodName}: {verslag}")
		self.assertLess(max(s for s, _, _ in metingen.values()), 2)


class TestMigratie(IntegrationTestCase):
	"""Bestaande regels en de teller. Draait in de hoofdthread zonder commit en eindigt met een rollback; wat het in
	zijn eigen transactie leest, nummert het eerst zelf (`commitnummer.deel_uit`)."""

	def setUp(self):
		ruim_op()
		self.ruimte = ruimte("Project Oud", {})

	def tearDown(self):
		frappe.db.rollback()
		ruim_op()

	def test_bestaande_regels_krijgen_hun_name_en_de_teller_begint_erboven(self):
		for i in range(3):
			api._log(self.ruimte, "nieuw", "Opslagmap", f"oud-{i}")
		oud = frappe.get_all(
			"Opslagwijziging", filters={"doel": ("like", "oud-%")}, pluck="name", order_by="name"
		)
		# Zoals vóór de migratie: geen nummers, en een teller die nog niet bestaat.
		frappe.db.sql("update `tabOpslagwijziging` set commitnummer = 0")
		frappe.db.sql("delete from `tabSeries` where name = %s", commitnummer.TELLER)
		commitnummer_invullen.execute()
		self.assertEqual(
			frappe.get_all("Opslagwijziging", filters={"name": ("in", oud)}, pluck="commitnummer", order_by="name"),
			oud,
		)
		hoogste = frappe.db.sql("select max(name) from `tabOpslagwijziging`")[0][0]
		self.assertEqual(
			frappe.db.sql("select current from `tabSeries` where name = %s", commitnummer.TELLER)[0][0], hoogste
		)
		# Een cursor van vóór de migratie (een name) blijft geldig: de rest komt erna.
		api._log(self.ruimte, "nieuw", "Opslagmap", "nieuw")
		commitnummer.deel_uit()
		uit = api.wijzigingen(ruimte=self.ruimte, sinds=oud[0])
		self.assertEqual(doelen(uit), ["oud-1", "oud-2", "nieuw"])
		self.assertEqual(uit["wijzigingen"][-1]["volgnummer"], hoogste + 1)

	def test_teller_zakt_nooit(self):
		frappe.db.sql(
			"insert into `tabSeries` (name, current) values (%s, 1000000) on duplicate key update current = 1000000",
			commitnummer.TELLER,
		)
		commitnummer.zorg_voor_teller()
		self.assertEqual(
			frappe.db.sql("select current from `tabSeries` where name = %s", commitnummer.TELLER)[0][0], 10**6
		)


class TestNummerVanBuiten(IntegrationTestCase):
	"""Wie een Opslagwijziging invoegt of opslaat (bijv. via /api/resource), kan het commitnummer niet zelf zetten."""

	def setUp(self):
		ruim_op()
		self.piet = gebruiker("piet")
		self.ruimte = ruimte("Project Buiten", {self.piet: "schrijver"})
		frappe.set_user(self.piet)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		ruim_op()

	def test_eigen_nummer_bij_invoegen_en_opslaan_telt_niet(self):
		begin = api.wijzigingen(ruimte=self.ruimte)["cursor"]
		doc = frappe.get_doc(
			{
				"doctype": "Opslagwijziging",
				"ruimte": self.ruimte,
				"soort": "eigenschappen",
				"doel_doctype": "Opslagmap",
				"doel": "zelf",
				"commitnummer": 10**9,
			}
		).insert()
		self.assertEqual(frappe.db.get_value("Opslagwijziging", doc.name, "commitnummer"), 0)
		commitnummer.deel_uit()
		nummer = frappe.db.get_value("Opslagwijziging", doc.name, "commitnummer")
		self.assertGreater(nummer, 0)
		self.assertLess(nummer, 10**9)
		self.assertEqual(api.wijzigingen(ruimte=self.ruimte, sinds=begin)["cursor"], nummer)
		doc.reload()
		doc.commitnummer = 10**9
		doc.save()
		self.assertEqual(frappe.db.get_value("Opslagwijziging", doc.name, "commitnummer"), nummer)

