"""Een pad met '/' in één verzoek naar het id van een map of bestand (voor delen en versturen vanuit de desktop-app)."""

import unicodedata

import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage import api
from openaec_storage.tests.hulp import NagebootsteS3, gebruiker, ruim_op, ruimte, upload


class TestZoekPad(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagdeling")
		self.s3 = NagebootsteS3().start()
		self.schrijver = gebruiker("schrijver")
		self.ruimte = ruimte("OpenAEC Studio", {self.schrijver: "schrijver"})
		frappe.set_user(self.schrijver)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Opslagdeling")
		self.s3.stop()
		ruim_op()

	def map(self, naam: str, ouder: str | None = None) -> str:
		return api.maak_map(ruimte=self.ruimte, naam=naam, bovenliggende=ouder)["map"]

	def dubbel(self, naam: str, ouder: str | None = None) -> str:
		"""Een tweede map met dezelfde naam op dezelfde plek, zoals ze van vóór de controle op dubbele mappen bestaan
		(de controle bij het opslaan gaat er buitenom)."""
		map = self.map(naam + " tijdelijk", ouder)
		frappe.db.set_value("Opslagmap", map, "naam", naam)
		return map

	def zoek(self, pad: str, **extra) -> str | None:
		return api.zoek_pad(ruimte=self.ruimte, pad=pad, **extra).get(extra.get("soort", "map"))

	def test_diep_pad_met_spaties_en_hoofdletters(self):
		a = self.map("50_projecten")
		b = self.map("05 Klant Noord", a)
		c = self.map("Fase 2 - Ontwerp", b)
		d = self.map("Tekeningen (definitief)", c)
		self.assertEqual(self.zoek("50_projecten/05 Klant Noord/Fase 2 - Ontwerp/Tekeningen (definitief)"), d)
		# Andere schrijfwijze, slashes van Windows en een slash aan het eind.
		self.assertEqual(self.zoek("50_PROJECTEN\\05 klant noord\\fase 2 - ontwerp\\tekeningen (definitief)\\"), d)
		self.assertEqual(api.zoek_pad(ruimte=self.ruimte, pad="50_projecten/Bestaat niet"), {"map": None, "gevonden": False})

	def test_hoofdniveau(self):
		self.assertEqual(api.zoek_pad(ruimte=self.ruimte, pad=""), {"map": None, "gevonden": True})

	def test_veel_broers_en_zussen(self):
		ouder = self.map("Archief")
		for i in range(300):
			self.map(f"Map {i:03d}", ouder)
		gezocht = self.map("Zzz laatste", ouder)
		self.assertEqual(self.zoek("Archief/Zzz laatste"), gezocht)
		self.assertEqual(self.zoek("Archief/map 150"), frappe.db.get_value("Opslagmap", {"naam": "Map 150"}))

	def test_unicode_in_een_andere_normalisatie(self):
		nfc = unicodedata.normalize("NFC", "Café ontwerp")
		map = self.map(nfc)
		self.assertEqual(self.zoek(unicodedata.normalize("NFD", "Café ontwerp")), map)

	def test_dubbele_mappen_van_vroeger_zoekt_in_allebei(self):
		# Van vóór de controle op dubbele mappen: twee keer "Projecten" op het hoofdniveau.
		eerste = self.map("Projecten")
		tweede = self.dubbel("Projecten")
		diep = self.map("Havenkwartier", tweede)
		self.assertEqual(self.zoek("Projecten/Havenkwartier"), diep)
		# Allebei passend aan het eind: de map met inhoud.
		upload(self.ruimte, "a.txt", b"a", map_naam=tweede)
		self.assertEqual(self.zoek("Projecten"), tweede)
		self.assertNotEqual(eerste, tweede)

	def test_precieze_schrijfwijze_gaat_voor(self):
		klein = self.map("tekeningen")
		groot = self.dubbel("Tekeningen")
		self.assertEqual(self.zoek("Tekeningen"), groot)
		self.assertEqual(self.zoek("tekeningen"), klein)

	def test_bestand(self):
		map = self.map("Fase 2")
		bestand = upload(self.ruimte, "Plan A.pdf", b"plan", map_naam=map)["bestand"]
		self.assertEqual(self.zoek("fase 2/plan a.pdf", soort="bestand"), bestand)
		self.assertIsNone(self.zoek("Fase 2/Plan B.pdf", soort="bestand"))
		api.verwijder(bestand=bestand)
		self.assertIsNone(self.zoek("Fase 2/Plan A.pdf", soort="bestand"))

	def test_verwijderde_map_telt_niet(self):
		map = self.map("Oud")
		api.verwijder_map(map=map)
		self.assertIsNone(self.zoek("Oud"))

	def test_alleen_met_leesrecht(self):
		self.map("Geheim")
		buiten = gebruiker("buiten")
		frappe.set_user(buiten)
		with self.assertRaises(frappe.PermissionError):
			self.zoek("Geheim")

	def test_in_een_gedeelde_map(self):
		gedeeld = self.map("Gedeeld")
		sub = self.map("Sub map", gedeeld)
		self.map("Buiten")
		extern = gebruiker("extern")
		deling = api.deel(doel_doctype="Opslagmap", doel=gedeeld, soort="gebruiker", gebruiker=extern, rechten="lezen")
		frappe.set_user(extern)
		self.assertEqual(api.zoek_pad(pad="sub map", deling=deling["deling"])["map"], sub)
		self.assertEqual(api.zoek_pad(pad="", deling=deling["deling"])["map"], gedeeld)
		self.assertFalse(api.zoek_pad(pad="Buiten", deling=deling["deling"])["gevonden"])

	def test_mogelijkheden_noemen_het(self):
		m = api.mogelijkheden()
		self.assertTrue(m["zoek_pad"])
		self.assertTrue(m["overdracht_van_opslag"])
