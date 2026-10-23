import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from openaec_storage import api, delen, namen
from openaec_storage.tests.hulp import NagebootsteS3, foutcode, gebruiker, ruim_op, ruimte, upload

ONGELDIG = "ongeldige_naam"


class TestOngeldigeNaam(UnitTestCase):
	def test_gereserveerde_namen_ook_met_extensie_en_in_alle_hoofdletters(self):
		for naam in ("nul", "NUL", "Nul", "con", "prn", "aux", "nul.txt", "con.log", "aux.tar.gz", "nul .txt",
			"COM1", "com9", "COM0", "lpt1", "LPT9.doc", "LPT0", "COM¹", "com²", "LPT³.txt", "CONIN$", "conout$"):
			with self.subTest(naam=naam):
				self.assertTrue(namen.ongeldige_naam(naam))
				self.assertIn("gereserveerde naam", namen.ongeldige_naam(naam))

	def test_namen_die_erop_lijken_zijn_goed(self):
		for naam in ("nul_", "nul_.txt", "null", "nullen.txt", "connect.txt", "auxiliary", "com10", "LPT", "com",
			"lpt1x.txt", "con-tract.pdf", "Console.log", ".nul", "x.nul", "COM1_", "plan (conflict Jan 2026-10-23 1200).pdf"):
			with self.subTest(naam=naam):
				self.assertIsNone(namen.ongeldige_naam(naam))

	def test_punt_of_spatie_aan_het_eind(self):
		for naam in ("rapport.", "rapport ", "a..", "..."):
			with self.subTest(naam=naam):
				self.assertIn("punt of spatie", namen.ongeldige_naam(naam))

	def test_verboden_tekens_en_controletekens(self):
		for teken in '<>:"/\\|?*':
			with self.subTest(teken=teken):
				self.assertIn(teken, namen.ongeldige_naam(f"a{teken}b.txt"))
		for naam in ("a\tb", "a\nb", "a\x00b", "a\x1fb"):
			with self.subTest(naam=repr(naam)):
				self.assertIn("controleteken", namen.ongeldige_naam(naam))

	def test_lengte_en_leeg(self):
		self.assertIsNone(namen.ongeldige_naam("a" * 255))
		self.assertIn("255", namen.ongeldige_naam("a" * 256))
		for naam in ("", ".", "..", None):
			self.assertTrue(namen.ongeldige_naam(naam))


class TestNamenInDeApi(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagdeling")
		self.s3 = NagebootsteS3().start()
		self.schrijver = gebruiker("schrijver")
		self.ruimte = ruimte("Project Namen", {self.schrijver: "schrijver"})
		frappe.set_user(self.schrijver)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.s3.stop()
		frappe.db.delete("Opslagdeling")
		ruim_op()

	def oud_bestand(self, naam: str, map: str | None = None) -> str:
		"""Een bestand met een naam van vóór de naamregels (bijv. gesynchroniseerd vanaf Linux)."""
		frappe.set_user("Administrator")
		goed = upload(self.ruimte, "tijdelijk.txt", b"oud", map_naam=map)
		frappe.db.set_value("Opslagbestand", goed["bestand"], "naam", naam)
		frappe.set_user(self.schrijver)
		return goed["bestand"]

	def test_nieuwe_upload_map_en_hernoemen_weigeren_een_ongeldige_naam(self):
		bestand = upload(self.ruimte, "goed.txt", b"x")["bestand"]
		map = api.maak_map(ruimte=self.ruimte, naam="Tekeningen")["map"]
		pogingen = [
			(api.upload_start, {"ruimte": self.ruimte, "naam": "nul", "grootte": 1}),
			(api.upload_start, {"ruimte": self.ruimte, "naam": "con.log", "grootte": 1, "map": map}),
			(api.upload_start, {"ruimte": self.ruimte, "naam": "plan.", "grootte": 1}),
			(api.upload_start, {"ruimte": self.ruimte, "naam": "a:b.txt", "grootte": 1}),
			(api.maak_map, {"ruimte": self.ruimte, "naam": "LPT1"}),
			(api.maak_map, {"ruimte": self.ruimte, "naam": "map."}),
			(api.verplaats, {"bestand": bestand, "naam": "aux.txt"}),
			(api.verplaats, {"bestand": bestand, "naam": "vraag?.txt", "map": map}),
			(api.verplaats_map, {"map": map, "naam": "COM²"}),
		]
		for functie, argumenten in pogingen:
			with self.subTest(functie=functie.__name__, argumenten=argumenten):
				self.assertEqual(foutcode(self, frappe.ValidationError, functie, **argumenten), ONGELDIG)
		with self.assertRaises(frappe.ValidationError) as fout:
			api.upload_start(ruimte=self.ruimte, naam="nul", grootte=1)
		self.assertIn("gereserveerde naam", str(fout.exception))

	def test_bestaand_bestand_met_een_oude_naam_blijft_en_kan_alleen_naar_een_geldige_naam(self):
		bestand = self.oud_bestand("nul")
		# Een nieuwe versie onder dezelfde naam mag (een andere computer die het bestand al had).
		versie = upload(self.ruimte, "nul", b"nieuw", basis_versie=1)
		self.assertEqual((versie["bestand"], versie["versie"], versie["conflict"]), (bestand, 2, False))
		self.assertEqual(foutcode(self, frappe.ValidationError, api.verplaats, bestand=bestand, naam="con"), ONGELDIG)
		self.assertEqual(api.verplaats(bestand=bestand, naam="nul_")["naam"], "nul_")

	def test_bestaande_map_met_een_oude_naam_blijft_op_te_vragen(self):
		map = api.maak_map(ruimte=self.ruimte, naam="tijdelijk")["map"]
		frappe.db.set_value("Opslagmap", map, "naam", "aux")
		self.assertEqual(api.maak_map(ruimte=self.ruimte, naam="aux")["map"], map)
		self.assertEqual(api.verplaats_map(map=map, naam="Hulp")["naam"], "Hulp")

	def test_batch_weigert_per_bestand(self):
		r = api.upload_klein_bytes(
			self.ruimte,
			[{"naam": "nul", "grootte": 1}, {"naam": "goed.txt", "grootte": 1}, {"naam": "a", "pad": "con/sub", "grootte": 1}],
			{"f0": b"x", "f1": b"y", "f2": b"z"},
		)["resultaten"]
		self.assertEqual([x["ok"] for x in r], [False, True, False])
		self.assertEqual([x.get("foutcode") for x in r], [ONGELDIG, None, ONGELDIG])
		self.assertFalse(frappe.db.exists("Opslagmap", {"ruimte": self.ruimte, "naam": "con"}))
		# Een bestaand bestand met een oude naam krijgt ook via de batch een nieuwe versie.
		self.oud_bestand("aux")
		r = api.upload_klein_bytes(self.ruimte, [{"naam": "aux", "grootte": 1, "basis_versie": 1}], {"f0": b"n"})
		self.assertEqual(r["resultaten"][0]["versie"], 2)

	def test_uploadlink_weigert_een_ongeldige_naam(self):
		map = api.maak_map(ruimte=self.ruimte, naam="Aanleveren")["map"]
		link = api.deel(doel_doctype="Opslagmap", doel=map, rechten="uploaden")
		frappe.set_user("Guest")
		self.assertEqual(
			foutcode(self, frappe.ValidationError, delen.upload_start, token=link["token"], naam="nul", grootte=1), ONGELDIG
		)
