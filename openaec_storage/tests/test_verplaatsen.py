import frappe
import requests
from frappe.tests import IntegrationTestCase

from openaec_storage import api
from openaec_storage.tests.hulp import NagebootsteS3, gebruiker, ruim_op, ruimte, upload


def namen(ruimte_naam, map_naam=None):
	inhoud = api.bladeren(ruimte=ruimte_naam, map=map_naam)
	return sorted(m["naam"] for m in inhoud["mappen"]), sorted(b["naam"] for b in inhoud["bestanden"])


class TestVerplaatsen(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		self.s3 = NagebootsteS3().start()
		self.schrijver = gebruiker("schrijver")
		self.lezer = gebruiker("lezer")
		self.ruimte = ruimte("Project V", {self.schrijver: "schrijver", self.lezer: "lezer"})
		frappe.set_user(self.schrijver)
		self.tekeningen = api.maak_map(ruimte=self.ruimte, naam="Tekeningen")["map"]

	def tearDown(self):
		self.s3.stop()
		ruim_op()

	def test_hernoemen_en_verplaatsen_houdt_het_bestand_id(self):
		b = upload(self.ruimte, "schets.dwg", b"x")
		r = api.verplaats(bestand=b["bestand"], naam="plattegrond.dwg", map=self.tekeningen)
		self.assertEqual(r["bestand"], b["bestand"])
		self.assertEqual(namen(self.ruimte), (["Tekeningen"], []))
		self.assertEqual(namen(self.ruimte, self.tekeningen), ([], ["plattegrond.dwg"]))
		soorten = [w["soort"] for w in api.wijzigingen(ruimte=self.ruimte, sinds=0)["wijzigingen"]]
		self.assertIn("verplaatst", soorten)

	def test_bestaand_doel_zonder_overschrijven_wordt_geweigerd(self):
		upload(self.ruimte, "a.txt", b"a")
		b = upload(self.ruimte, "b.txt", b"b")
		with self.assertRaises(frappe.ValidationError):
			api.verplaats(bestand=b["bestand"], naam="a.txt", map=None)

	def test_overschrijven_maakt_een_nieuwe_versie_van_het_doel(self):
		# Opslaan via een tijdelijk bestand en hernoemen: het origineel krijgt er een versie bij.
		origineel = upload(self.ruimte, "rapport.docx", b"versie een")
		tijdelijk = upload(self.ruimte, "~rapport.tmp", b"versie twee")
		r = api.verplaats(bestand=tijdelijk["bestand"], naam="rapport.docx", map=None, overschrijf=1)
		self.assertEqual(r["bestand"], origineel["bestand"])
		self.assertEqual(r["versie"], 2)
		self.assertEqual(namen(self.ruimte), (["Tekeningen"], ["rapport.docx"]))
		self.assertFalse(frappe.db.exists("Opslagbestand", tijdelijk["bestand"]))
		url = api.download(bestand=origineel["bestand"])["url"]
		self.assertEqual(requests.get(url, timeout=30).content, b"versie twee")
		self.assertEqual(len(api.versies(bestand=origineel["bestand"])), 2)

	def test_overschrijven_van_een_door_een_ander_vergrendeld_bestand_mag_niet(self):
		origineel = upload(self.ruimte, "slot.txt", b"1")
		ander = gebruiker("ander")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.append("leden", {"gebruiker": ander, "rol": "schrijver"})
		doc.save(ignore_permissions=True)
		frappe.set_user(ander)
		api.vergrendel(bestand=origineel["bestand"])
		frappe.set_user(self.schrijver)
		tijdelijk = upload(self.ruimte, "slot.tmp", b"2")
		with self.assertRaises(frappe.PermissionError):
			api.verplaats(bestand=tijdelijk["bestand"], naam="slot.txt", map=None, overschrijf=1)

	def test_lezer_mag_niet_verplaatsen(self):
		b = upload(self.ruimte, "c.txt", b"c")
		frappe.set_user(self.lezer)
		with self.assertRaises(frappe.PermissionError):
			api.verplaats(bestand=b["bestand"], naam="d.txt", map=None)

	def test_map_hernoemen_en_verplaatsen(self):
		archief = api.maak_map(ruimte=self.ruimte, naam="Archief")["map"]
		api.verplaats_map(map=self.tekeningen, naam="Tekeningen 2025", bovenliggende=archief)
		self.assertEqual(namen(self.ruimte), (["Archief"], []))
		self.assertEqual(namen(self.ruimte, archief), (["Tekeningen 2025"], []))

	def test_map_niet_in_zichzelf_verplaatsen(self):
		sub = api.maak_map(ruimte=self.ruimte, naam="Sub", bovenliggende=self.tekeningen)["map"]
		with self.assertRaises(frappe.ValidationError):
			api.verplaats_map(map=self.tekeningen, naam="Tekeningen", bovenliggende=sub)

	def test_map_verwijderen_gaat_met_inhoud_naar_de_prullenbak(self):
		sub = api.maak_map(ruimte=self.ruimte, naam="Sub", bovenliggende=self.tekeningen)["map"]
		b = upload(self.ruimte, "diep.txt", b"d", map_naam=sub)
		api.verwijder_map(map=self.tekeningen)
		self.assertEqual(namen(self.ruimte), ([], []))
		self.assertTrue(frappe.db.get_value("Opslagbestand", b["bestand"], "verwijderd_op"))
		self.assertTrue(frappe.db.get_value("Opslagmap", sub, "verwijderd_op"))
