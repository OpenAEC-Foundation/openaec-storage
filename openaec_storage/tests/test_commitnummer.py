"""Het commitnummer van de wijzigingenlijst (ontwerp samenwerken §5.4, stap 4b): de veger nummert bij het lezen.

Elke `Verbinding` is een eigen thread met een eigen Frappe-context en databaseverbinding, zoals een verzoek of taak op
een tweede werker. De test bepaalt stap voor stap wie invoegt, wie commit en wie terugdraait; wat niet gecommit is,
blijft open staan tot de volgende stap. Wat een andere verbinding moet zien, is echt gecommit, dus deze tests ruimen
hun gegevens zelf op. De hoofdthread leest alleen, telkens met een verse transactie; de veger commit daar zelf.

De isolatie is die van productie: REPEATABLE-READ met `innodb_snapshot_isolation` aan (`test_isolatie_is_die_van_
productie`). Elke proef eindigt met: de vastgelegde regel staat precies één keer in `wijzigingen(sinds=0)`, en haar
nummer ligt boven de cursor die de client al kreeg."""

import queue
import random
import threading
import time
from unittest import mock

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_test_client

from openaec_storage import api, commitnummer, ruimtebeheer
from openaec_storage.patches import commitnummer_invullen
from openaec_storage.tests.hulp import gebruiker, nummer_wijzigingen, ruim_op, ruimte

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


def houd_teller_vast():
	frappe.db.sql("select current from `tabSeries` where name = %s for update", commitnummer.TELLER)


def korte_wachttijd():
	frappe.db.sql("set session innodb_lock_wait_timeout = 1")


def lees_vanaf(ruimte_naam: str, cursor: int, als: str, limiet: int = 1000) -> dict:
	"""Een client die leest, in een verse transactie, zoals een nieuw verzoek."""
	frappe.db.rollback()
	frappe.set_user(als)
	try:
		return api.wijzigingen(ruimte=ruimte_naam, sinds=cursor, limiet=limiet)
	finally:
		frappe.set_user("Administrator")


def getimed(functie):
	begin = time.perf_counter()
	uit = functie()
	return uit, time.perf_counter() - begin


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

	# --- lezen in de hoofdthread, telkens met een verse transactie ----------------------------------------

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

	def bestaat(self, doctype: str, name: str) -> bool:
		frappe.db.rollback()
		return bool(frappe.db.exists(doctype, name))

	def teller(self) -> int:
		frappe.db.rollback()
		return frappe.db.sql("select current from `tabSeries` where name = %s", commitnummer.TELLER)[0][0]

	def precies_eens(self, doel: str, cursor: int, ruimte_naam: str | None = None) -> int:
		"""Het einde van elke proef: de vastgelegde regel staat precies één keer in `wijzigingen(sinds=0)`, en wie vanaf
		de cursor leest die hij al kreeg, krijgt haar ook precies één keer, met een hoger nummer. Geeft het nummer."""
		ruimte_naam = ruimte_naam or self.r1
		self.assertEqual(doelen(self.lees(ruimte_naam, 0)).count(doel), 1, f"{doel} niet precies één keer")
		vanaf = [w for w in self.lees(ruimte_naam, cursor)["wijzigingen"] if w["doel"] == doel]
		self.assertEqual(len(vanaf), 1, f"{doel} niet precies één keer vanaf cursor {cursor}")
		self.assertGreater(vanaf[0]["volgnummer"], cursor)
		return vanaf[0]["volgnummer"]

	def geen_dubbele_nummers(self):
		frappe.db.rollback()
		dubbel = frappe.db.sql(
			"""select commitnummer, count(*) from `tabOpslagwijziging` where commitnummer > 0
			group by commitnummer having count(*) > 1"""
		)
		self.assertEqual(dubbel, ())


def doelen(antwoord: dict) -> list[str]:
	return [w["doel"] for w in antwoord["wijzigingen"]]


class TestCommitnummer(Basis):
	def test_isolatie_is_die_van_productie(self):
		a = self.verbinding("A")
		self.assertEqual(
			a.doe(lambda: frappe.db.sql("select @@transaction_isolation, @@innodb_snapshot_isolation")[0]),
			("REPEATABLE-READ", 1),
		)

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
		# A voegde eerst in (lagere name) maar werd pas na de eerste lezing zichtbaar: A krijgt het hogere nummer.
		self.assertLess(eerst_ingevoegd, later_ingevoegd)
		self.assertGreater(self.nummer(eerst_ingevoegd), self.nummer(later_ingevoegd))
		self.assertGreater(self.precies_eens("a.ifc", eerste["cursor"]), eerste["cursor"])

	def test_teruggedraaide_transactie_geeft_geen_gat_en_geen_nummer(self):
		a, b = self.verbinding("A"), self.verbinding("B")
		begin = self.lees(self.r1)["cursor"]
		voor = self.teller()
		b.doe(lambda: schrijf(self.r1, "blijft.ifc"))
		weg = a.doe(lambda: schrijf(self.r1, "weg.ifc"))
		a.doe(rollback)
		b.doe(commit)
		a.doe(lambda: schrijf(self.r1, "daarna.ifc"))
		a.doe(commit)
		uit = self.lees(self.r1, begin)
		self.assertEqual(doelen(uit), ["blijft.ifc", "daarna.ifc"])
		volgnummers = [w["volgnummer"] for w in uit["wijzigingen"]]
		self.assertEqual(volgnummers, [voor + 1, voor + 2], "een rollback laat geen gat")
		self.assertIsNone(self.nummer(weg))
		self.assertEqual(self.teller(), voor + 2)

	def test_twee_ruimtes_krijgen_elk_hun_eigen_regels_met_unieke_nummers(self):
		a, b = self.verbinding("A"), self.verbinding("B")
		c1, c2 = self.lees(self.r1)["cursor"], self.lees(self.r2)["cursor"]
		namen = {}
		namen["een-1"] = a.doe(lambda: schrijf(self.r1, "een-1"))
		namen["twee-1"] = b.doe(lambda: schrijf(self.r2, "twee-1"))
		b.doe(commit)
		self.lees(self.r1)  # de veger nummert over de hele site, ook de regel van de andere ruimte
		namen["een-2"] = a.doe(lambda: schrijf(self.r1, "een-2"))
		a.doe(commit)
		self.lees(self.r2)
		namen["twee-2"] = b.doe(lambda: schrijf(self.r2, "twee-2"))
		b.doe(commit)
		een, twee = self.lees(self.r1, c1), self.lees(self.r2, c2)
		self.assertEqual(doelen(een), ["een-1", "een-2"])
		self.assertEqual(doelen(twee), ["twee-1", "twee-2"])
		# Over de hele site in de volgorde waarin de regels zichtbaar werden; een gat in de nummers van een ruimte is
		# een regel van de andere.
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
		self.precies_eens("laat.ifc", leeg["cursor"])

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

	def test_taak_die_tussendoor_commit_mist_niets(self):
		"""Een taak commit tussendoor (ook met `commit(chain=True)`), een ander verzoek ertussen; een client leest na
		elke commit vanaf zijn cursor."""
		a, b = self.verbinding("taak"), self.verbinding("ander")
		cursor = begin = self.lees(self.r1)["cursor"]

		def stap(doel: str, keten: bool = False):
			schrijf(self.r1, doel)
			frappe.db.commit(chain=keten)

		gezien = []
		for wie, doel, keten in (
			(a, "taak-1", True),
			(b, "ander", False),
			(a, "taak-2", True),
			(a, "taak-3", False),
		):
			wie.doe(lambda d=doel, k=keten: stap(d, k))
			uit = self.lees(self.r1, cursor)
			gezien += doelen(uit)
			cursor = uit["cursor"]
		self.assertEqual(gezien, ["taak-1", "ander", "taak-2", "taak-3"])
		for doel in gezien:
			self.precies_eens(doel, begin)

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

	def test_lezen_zonder_nieuwe_regels_neemt_geen_slot(self):
		"""Een batch die helemaal terug ging naar een savepoint, laat niets achter. Een lezer vindt dan geen regel met
		nummer 0, en raakt de teller niet aan: hij wacht ook niet als een ander het tellerslot heeft."""
		a, b, lezer = self.verbinding("A"), self.verbinding("B"), self.verbinding("L")
		begin = self.lees(self.r1)["cursor"]
		voor = self.teller()

		def batch():
			frappe.db.savepoint("alles")
			schrijf(self.r1, "terug")
			frappe.db.rollback(save_point="alles")
			frappe.db.commit()

		a.doe(batch)
		b.doe(houd_teller_vast)
		lezer.doe(korte_wachttijd)
		uit, duur = lezer.doe(lambda: getimed(lambda: lees_vanaf(self.r1, begin, self.anna)))
		self.assertEqual((uit["wijzigingen"], uit["cursor"]), ([], begin))
		self.assertLess(duur, 0.5)
		b.doe(rollback)
		self.assertEqual(self.teller(), voor)

	def test_schrijver_neemt_het_tellerslot_nooit(self):
		"""Sol, PR #16 ronde 2, punt 3: wie het tellerslot bij het schrijven nam, kon in een kring van wachtende
		transacties komen. Nu schrijft en commit een schrijver terwijl een ander het tellerslot heeft, zonder te wachten
		(wachttijd 1 s). Alleen een veger wacht op het tellerslot."""
		a, b, lezer = self.verbinding("A"), self.verbinding("B"), self.verbinding("L")
		begin = self.lees(self.r1)["cursor"]
		b.doe(houd_teller_vast)

		def map_maken():
			korte_wachttijd()
			map_id = api.maak_map(ruimte=self.r1, naam="Tijdens het slot")["map"]
			frappe.db.commit()
			return map_id

		map_id, duur = a.doe(lambda: getimed(map_maken))
		self.assertLess(duur, 0.5)
		self.assertEqual(self.nummer(self.name_van(map_id)), 0)
		lezer.doe(korte_wachttijd)
		with self.assertRaises(frappe.QueryTimeoutError):
			lezer.doe(lambda: lees_vanaf(self.r1, begin, self.anna))
		b.doe(rollback)
		self.precies_eens(map_id, begin)

	def test_veger_wacht_niet_op_een_open_schrijftransactie(self):
		"""De veger nummert alleen vastgelegde regels, met een gewone lezing; de open regel van een schrijver laat hij
		liggen. Gemeten met een wachttijd van 1 s: hij wacht niet. Na de commit krijgt de open regel een nummer boven de
		cursor die de client al kreeg."""
		a, b, lezer = self.verbinding("A"), self.verbinding("B"), self.verbinding("L")
		begin = self.lees(self.r1)["cursor"]
		open_regel = a.doe(lambda: schrijf(self.r1, "open.ifc"))
		dicht_regel = b.doe(lambda: schrijf(self.r1, "dicht.ifc"))
		b.doe(commit)
		lezer.doe(korte_wachttijd)
		eerste, duur = lezer.doe(lambda: getimed(lambda: lees_vanaf(self.r1, begin, self.anna)))
		self.assertEqual(doelen(eerste), ["dicht.ifc"])
		self.assertLess(duur, 0.5)
		self.assertEqual(self.nummer(open_regel), None, "de open regel is voor anderen nog onzichtbaar")
		a.doe(commit)
		tweede = lezer.doe(lambda: lees_vanaf(self.r1, eerste["cursor"], self.anna))
		self.assertEqual(doelen(tweede), ["open.ifc"])
		self.assertLess(open_regel, dicht_regel)
		self.assertGreater(self.precies_eens("open.ifc", eerste["cursor"]), self.nummer(dicht_regel))

	def test_twee_vegers_tegelijk_geven_geen_dubbel_nummer(self):
		"""Twee vegers vanaf twee verbindingen wachten tegelijk op het tellerslot; terwijl ze wachten, komen er regels
		bij. De eerste nummert alles wat vastgelegd is; de tweede ziet daarna de nieuwe teller en niets meer om te
		nummeren. Beide clients krijgen dezelfde nummers, en die verschuiven daarna niet."""
		a, b = self.verbinding("A"), self.verbinding("B")
		l1, l2 = self.verbinding("L1"), self.verbinding("L2")
		begin = self.lees(self.r1)["cursor"]

		def tien(voorvoegsel: str) -> list[str]:
			doelen_ = [f"{voorvoegsel}-{i}" for i in range(10)]
			for doel in doelen_:
				schrijf(self.r1, doel)
			frappe.db.commit()
			return doelen_

		geschreven = a.doe(lambda: tien("voor"))
		b.doe(houd_teller_vast)
		wachtend = [lezer.begin(lambda: getimed(lambda: lees_vanaf(self.r1, begin, self.anna))) for lezer in (l1, l2)]
		time.sleep(0.5)
		geschreven += a.doe(lambda: tien("tussen"))
		b.doe(rollback)
		(u1, d1), (u2, d2) = (uitkomst_van(q) for q in wachtend)
		self.assertGreater(min(d1, d2), 0.4, "beide vegers wachtten op het tellerslot")
		nummers = {w["doel"]: w["volgnummer"] for w in u1["wijzigingen"]}
		self.assertEqual(sorted(nummers), sorted(geschreven))
		self.assertEqual({w["doel"]: w["volgnummer"] for w in u2["wijzigingen"]}, nummers)
		self.assertEqual(len(set(nummers.values())), 20)
		self.assertEqual({w["doel"]: w["volgnummer"] for w in self.lees(self.r1, begin)["wijzigingen"]}, nummers)
		self.geen_dubbele_nummers()

	def test_activiteit_sorteert_en_nummert_op_het_commitnummer(self):
		a, b = self.verbinding("A"), self.verbinding("B")
		a.doe(lambda: schrijf(self.r1, "a.ifc"))
		b.doe(lambda: schrijf(self.r1, "b.ifc"))
		b.doe(commit)
		frappe.db.rollback()
		frappe.set_user(self.anna)
		try:
			ruimtebeheer.activiteit(self.r1)  # de veger nummert b.ifc; a.ifc is nog open
			a.doe(commit)
			frappe.db.rollback()
			activiteit = ruimtebeheer.activiteit(self.r1, aantal=500)
		finally:
			frappe.set_user("Administrator")
		eigen = [(r["doel"], r["volgnummer"]) for r in activiteit["regels"] if r["doel"] in ("a.ifc", "b.ifc")]
		lijst = self.lees(self.r1)["wijzigingen"]
		uit_lijst = {w["doel"]: w["volgnummer"] for w in lijst}
		# Nieuwste eerst: A werd als laatste zichtbaar. Hetzelfde nummer als in de wijzigingenlijst.
		self.assertEqual([d for d, _ in eigen], ["a.ifc", "b.ifc"])
		self.assertEqual(dict(eigen), {d: uit_lijst[d] for d in ("a.ifc", "b.ifc")})

	def test_gelijktijdig_schrijven_en_lezen_mist_niets(self):
		"""Drie schrijvers met korte transacties (soms tussen invoegen en commit even wachten) en twee lezers die elk
		steeds vanaf hun eigen cursor lezen, dus twee vegers. Elke lezer krijgt elke regel precies één keer, in
		oplopende volgorde."""
		schrijvers = [self.verbinding(f"S{i}") for i in range(3)]
		lezers = [self.verbinding(f"L{i}") for i in range(2)]
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

		bezig = [s.begin(lambda w=s.name: schrijf_veel(w)) for s in schrijvers]
		alles_geschreven = threading.Event()

		def lees_alles() -> tuple[list[str], list[int]]:
			gezien, volgnummers, cursor = [], [], begin
			while True:
				# Zijn alle schrijvers al klaar vóór deze lezing, dan ziet ze alles; lees dan door tot de laatste pagina.
				klaar = alles_geschreven.is_set()
				uit = lees_vanaf(self.r1, cursor, self.anna, limiet=7)
				gezien += doelen(uit)
				volgnummers += [w["volgnummer"] for w in uit["wijzigingen"]]
				cursor = uit["cursor"]
				if klaar and not uit["meer"]:
					return gezien, volgnummers

		lezend = [lezer.begin(lees_alles) for lezer in lezers]
		geschreven = [d for q in bezig for d in uitkomst_van(q)]
		alles_geschreven.set()
		for q in lezend:
			gezien, volgnummers = uitkomst_van(q)
			self.assertEqual(sorted(gezien), sorted(geschreven), "een lezer mist regels of krijgt ze dubbel")
			self.assertEqual(volgnummers, sorted(set(volgnummers)))
		self.geen_dubbele_nummers()
		print(f"\n  {self._testMethodName}: {len(geschreven)} regels, beide lezers zagen ze allemaal precies één keer")

	def test_veging_duurt_kort_ook_bij_een_batch(self):
		"""Meting: hoe lang een veging duurt (van de eerste lezing tot de commit; het tellerslot duurt korter), bij 1,
		100 en 500 vastgelegde regels zonder nummer."""
		a, lezer = self.verbinding("A"), self.verbinding("L")

		def batch(n: int):
			for i in range(n):
				schrijf(self.r1, f"batch-{n}-{i}")
			frappe.db.commit()

		def veeg():
			frappe.db.rollback()
			return getimed(commitnummer.veeg)[1]

		metingen = {}
		for n in (1, 100, 500):
			a.doe(lambda n=n: batch(n))
			metingen[n] = lezer.doe(veeg)
		print(
			f"\n  {self._testMethodName}: "
			+ ", ".join(f"{n} regels: {duur * 1000:.1f} ms" for n, duur in metingen.items())
		)
		self.assertLess(max(metingen.values()), 2)
		frappe.db.rollback()
		self.assertEqual(frappe.db.count("Opslagwijziging", {"commitnummer": 0}), 0)

	def test_halve_veging_komt_nooit_vast(self):
		"""Mislukt de veging halverwege, dan draait de veger zijn transactie terug: ook een aanroeper die de fout opvangt
		en daarna commit, legt geen half genummerde regels vast. De volgende lezing nummert alles."""
		a, lezer = self.verbinding("A"), self.verbinding("L")
		begin = self.lees(self.r1)["cursor"]
		voor = self.teller()

		def twee():
			namen = [schrijf(self.r1, "half-1"), schrijf(self.r1, "half-2")]
			frappe.db.commit()
			return namen

		namen = a.doe(twee)

		def slordige_aanroeper():
			echte, keer = frappe.db.sql, []

			def sql(query, *args, **kwargs):
				if str(query).startswith("update `tabOpslagwijziging`"):
					keer.append(1)
					if len(keer) == 2:
						raise frappe.QueryTimeoutError("nagebootst")
				return echte(query, *args, **kwargs)

			frappe.db.rollback()
			with mock.patch.object(frappe.db, "sql", sql):
				try:
					commitnummer.veeg()
				except frappe.QueryTimeoutError:
					pass
			frappe.db.commit()

		lezer.doe(slordige_aanroeper)
		self.assertEqual([self.nummer(n) for n in namen], [0, 0])
		self.assertEqual(self.teller(), voor)
		self.assertEqual(doelen(self.lees(self.r1, begin)), ["half-1", "half-2"])
		self.assertEqual([self.nummer(n) for n in namen], [voor + 1, voor + 2])

	def test_veger_begint_opnieuw_na_een_deadlock(self):
		"""Bij fout 1213 (of 1020, in Frappe allebei QueryDeadlockError) begint de veger opnieuw bij de commit."""
		a, lezer = self.verbinding("A"), self.verbinding("L")
		begin = self.lees(self.r1)["cursor"]
		voor = self.teller()
		regel = a.doe(lambda: (schrijf(self.r1, "opnieuw.ifc"), frappe.db.commit())[0])

		def met_een_deadlock():
			echte, keer = frappe.db.sql, []

			def sql(query, *args, **kwargs):
				if "for update" in str(query) and not keer:
					keer.append(1)
					raise frappe.QueryDeadlockError("nagebootst")
				return echte(query, *args, **kwargs)

			with mock.patch.object(frappe.db, "sql", sql):
				return lees_vanaf(self.r1, begin, self.anna), len(keer)

		uit, deadlocks = lezer.doe(met_een_deadlock)
		self.assertEqual((doelen(uit), deadlocks), (["opnieuw.ifc"], 1))
		self.assertEqual((self.nummer(regel), self.teller()), (voor + 1, voor + 1))

	def test_veger_weigert_een_transactie_met_schrijfwerk(self):
		"""Een lezer schrijft zelf niets. Staat er toch schrijfwerk in de transactie, dan commit de veger die niet."""
		a = self.verbinding("A")

		def lezen_na_schrijven():
			schrijf(self.r1, "eigen.ifc")
			try:
				api.wijzigingen(ruimte=self.r1)
			finally:
				frappe.db.rollback()

		with self.assertRaisesRegex(RuntimeError, "committen"):
			a.doe(lezen_na_schrijven)
		self.assertIsNone(self.name_van("eigen.ifc"))

	def test_get_verzoek_via_de_http_laag_houdt_de_nummers(self):
		"""Een GET-verzoek via de echte WSGI-app van Frappe (`frappe.app.application`, met de werkzeug-testclient in
		een eigen thread, zoals Frappe's eigen API-tests). Frappe draait na een GET terug (`sync_database`); de nummers
		van de veger staan daarna toch in de database, en een volgende lezing geeft dezelfde nummers."""
		a = self.verbinding("A")
		begin = self.lees(self.r1)["cursor"]

		def inloggen() -> str:
			from frappe.auth import CookieManager, LoginManager
			from frappe.utils import set_request

			set_request(path="/")
			frappe.local.cookie_manager = CookieManager()
			frappe.local.login_manager = LoginManager()
			frappe.local.login_manager.login_as(self.anna)
			frappe.db.commit()
			return frappe.session.sid

		sid = a.doe(inloggen)
		regel = a.doe(lambda: (schrijf(self.r1, "via-http.ifc"), frappe.db.commit())[0])
		self.assertEqual(self.nummer(regel), 0)
		antwoord = {}

		def verzoek():
			antwoord["uit"] = get_test_client().get(
				"/api/method/openaec_storage.api.wijzigingen",
				query_string={"ruimte": self.r1, "sinds": begin, "sid": sid},
				headers={"X-Frappe-Site-Name": self.site},
			)

		draad = threading.Thread(target=verzoek)
		draad.start()
		draad.join(WACHT)
		uit = antwoord["uit"]
		self.assertEqual(uit.status_code, 200, uit.text)
		bericht = uit.json["message"]
		self.assertEqual(doelen(bericht), ["via-http.ifc"])
		gekregen = bericht["wijzigingen"][0]["volgnummer"]
		self.assertGreater(gekregen, begin)
		self.assertEqual(self.nummer(regel), gekregen, "het nummer staat na het GET-verzoek in de database")
		self.assertEqual(self.precies_eens("via-http.ifc", begin), gekregen)

		def uitloggen():
			from frappe.sessions import delete_session

			delete_session(sid, reason="test")
			frappe.db.commit()

		a.doe(uitloggen)


class TestCommitroutes(Basis):
	"""De proeven van Sol op PR #16, ronde 2: commitroutes buiten `before_commit` om. Op 71c4446 legde elke route een
	regel vast met nummer 0 die de wijzigingenlijst nooit gaf. Nu nummert de veger hem bij de volgende lezing."""

	def test_a_after_rollback_callback_maakt_een_map(self):
		"""Een `after_rollback`-callback maakt een map; daarna een andere map en een volledige rollback, dan een gewone
		commit. Frappe begint vóór de callbacks al een nieuwe transactie: de map van de callback komt daarin."""
		a = self.verbinding("A")
		begin = self.lees(self.r1)["cursor"]
		gemaakt = {}

		def proef():
			frappe.db.after_rollback.add(
				lambda: gemaakt.update(callback=api.maak_map(ruimte=self.r1, naam="Uit de callback")["map"])
			)
			gemaakt["terug"] = api.maak_map(ruimte=self.r1, naam="Teruggedraaid")["map"]
			frappe.db.rollback()
			frappe.db.commit()

		a.doe(proef)
		self.assertTrue(self.bestaat("Opslagmap", gemaakt["callback"]))
		self.assertIsNone(self.name_van(gemaakt["terug"]))
		self.assertNotIn(gemaakt["terug"], doelen(self.lees(self.r1, 0)))
		self.precies_eens(gemaakt["callback"], begin)

	def test_b_rauwe_commit_en_dan_rollback(self):
		"""`maak_map()`, dan `frappe.db.sql("commit")`, dan `frappe.db.rollback()`: map en regel zijn vastgelegd."""
		a = self.verbinding("A")
		begin = self.lees(self.r1)["cursor"]

		def proef():
			map_id = api.maak_map(ruimte=self.r1, naam="Rauw")["map"]
			frappe.db.sql("commit")
			frappe.db.rollback()
			return map_id

		map_id = a.doe(proef)
		self.assertTrue(self.bestaat("Opslagmap", map_id))
		self.precies_eens(map_id, begin)

	def test_c_before_commit_callback_commit_rauw_en_gooit(self):
		"""Een `before_commit`-callback doet een rauwe SQL-commit en gooit dan; de aanroeper draait terug."""
		a = self.verbinding("A")
		begin = self.lees(self.r1)["cursor"]

		def callback():
			frappe.db.sql("commit")
			raise frappe.ValidationError("na de rauwe commit")

		def proef():
			map_id = api.maak_map(ruimte=self.r1, naam="Callback")["map"]
			frappe.db.before_commit.add(callback)
			try:
				frappe.db.commit()
			except frappe.ValidationError:
				frappe.db.rollback()
			return map_id

		map_id = a.doe(proef)
		self.assertTrue(self.bestaat("Opslagmap", map_id))
		self.precies_eens(map_id, begin)

	def test_d_mislukte_rename_table_commit_de_map(self):
		"""`maak_map()`, dan `RENAME TABLE` op een tabel die niet bestaat: fout 1146, maar MariaDB commit eerst."""
		a = self.verbinding("A")
		begin = self.lees(self.r1)["cursor"]

		def proef():
			map_id = api.maak_map(ruimte=self.r1, naam="Hernoemd")["map"]
			try:
				frappe.db.sql("rename table `tabBestaatNiet` to `tabBestaatNietOok`")
			except frappe.db.ProgrammingError as fout:
				code = fout.args[0]
			frappe.db.rollback()
			return map_id, code

		map_id, code = a.doe(proef)
		self.assertEqual(code, 1146)
		self.assertTrue(self.bestaat("Opslagmap", map_id))
		self.precies_eens(map_id, begin)

	def test_e_mislukte_alter_table_na_een_savepoint(self):
		"""`maak_map()`, een savepoint en een rollback daarheen (Frappe zet `transaction_writes` dan op 0), dan een
		mislukte `ALTER TABLE`: Frappe houdt hem niet tegen, en MariaDB commit eerst."""
		a = self.verbinding("A")
		begin = self.lees(self.r1)["cursor"]

		def proef():
			map_id = api.maak_map(ruimte=self.r1, naam="Gewijzigd")["map"]
			frappe.db.savepoint("na_de_map")
			frappe.db.rollback(save_point="na_de_map")
			schrijfwerk = frappe.db.transaction_writes
			try:
				frappe.db.sql("alter table `tabBestaatNiet` add column x int")
			except frappe.db.ProgrammingError as fout:
				code = fout.args[0]
			frappe.db.rollback()
			return map_id, code, schrijfwerk

		map_id, code, schrijfwerk = a.doe(proef)
		self.assertEqual((code, schrijfwerk), (1146, 0))
		self.assertTrue(self.bestaat("Opslagmap", map_id))
		self.precies_eens(map_id, begin)


class TestMigratie(IntegrationTestCase):
	"""Bestaande regels en de teller. Draait in de hoofdthread zonder commit en eindigt met een rollback; wat het in
	zijn eigen transactie leest, nummert het eerst zelf (`hulp.nummer_wijzigingen`)."""

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
		nummer_wijzigingen()
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
		nummer_wijzigingen()
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
		nummer_wijzigingen()
		nummer = frappe.db.get_value("Opslagwijziging", doc.name, "commitnummer")
		self.assertGreater(nummer, 0)
		self.assertLess(nummer, 10**9)
		self.assertEqual(api.wijzigingen(ruimte=self.ruimte, sinds=begin)["cursor"], nummer)
		doc.reload()
		doc.commitnummer = 10**9
		doc.save()
		self.assertEqual(frappe.db.get_value("Opslagwijziging", doc.name, "commitnummer"), nummer)
