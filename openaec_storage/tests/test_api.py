import frappe
import requests
from frappe.tests import IntegrationTestCase

from openaec_storage import api
from openaec_storage.tests.hulp import NagebootsteS3, gebruiker, ruim_op, ruimte, upload

MIB = 1024 * 1024


class TestApi(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		self.s3 = NagebootsteS3().start()
		self.schrijver = gebruiker("schrijver")
		self.lezer = gebruiker("lezer")
		self.ruimte = ruimte("Project B", {self.schrijver: "schrijver", self.lezer: "lezer"})
		frappe.set_user(self.schrijver)

	def tearDown(self):
		self.s3.stop()
		ruim_op()

	def test_upload_en_download_komen_overeen(self):
		inhoud = b"IFC-model " * 1000
		resultaat = upload(self.ruimte, "model.ifc", inhoud)
		self.assertEqual(resultaat["versie"], 1)
		self.assertFalse(resultaat["conflict"])
		url = api.download(bestand=resultaat["bestand"])["url"]
		self.assertEqual(requests.get(url, timeout=30).content, inhoud)

	def test_groot_bestand_gaat_in_delen_van_8_mib(self):
		inhoud = b"x" * (8 * MIB + 10)
		start = api.upload_start(ruimte=self.ruimte, naam="wolk.e57", grootte=len(inhoud))
		self.assertEqual(start["deelgrootte"], 8 * MIB)
		self.assertEqual([d["nummer"] for d in start["delen"]], [1, 2])

	def test_tweede_upload_op_huidige_versie_wordt_versie_2(self):
		eerste = upload(self.ruimte, "plan.pdf", b"versie een")
		tweede = upload(self.ruimte, "plan.pdf", b"versie twee", basis_versie=eerste["versie"])
		self.assertEqual(tweede["bestand"], eerste["bestand"])
		self.assertEqual(tweede["versie"], 2)
		self.assertEqual(len(api.versies(bestand=eerste["bestand"])), 2)

	def test_verouderde_basisversie_geeft_conflictkopie(self):
		eerste = upload(self.ruimte, "notitie.txt", b"a")
		upload(self.ruimte, "notitie.txt", b"b", basis_versie=1)
		conflict = upload(self.ruimte, "notitie.txt", b"c", basis_versie=1)
		self.assertTrue(conflict["conflict"])
		self.assertNotEqual(conflict["bestand"], eerste["bestand"])
		naam = frappe.db.get_value("Opslagbestand", conflict["bestand"], "naam")
		self.assertIn("(conflict", naam)

	def test_nieuw_bestand_met_bestaande_naam_zonder_basis_is_conflict(self):
		upload(self.ruimte, "dubbel.txt", b"1")
		tweede = upload(self.ruimte, "dubbel.txt", b"2")
		self.assertTrue(tweede["conflict"])

	def test_lezer_mag_niet_uploaden(self):
		frappe.set_user(self.lezer)
		with self.assertRaises(frappe.PermissionError):
			api.upload_start(ruimte=self.ruimte, naam="x.txt", grootte=1)

	def test_lezer_mag_wel_downloaden(self):
		resultaat = upload(self.ruimte, "lees.txt", b"inhoud")
		frappe.set_user(self.lezer)
		self.assertIn("url", api.download(bestand=resultaat["bestand"]))

	def test_verwijderen_en_herstellen(self):
		resultaat = upload(self.ruimte, "weg.txt", b"x")
		api.verwijder(bestand=resultaat["bestand"])
		namen = [b["naam"] for b in api.bladeren(ruimte=self.ruimte)["bestanden"]]
		self.assertNotIn("weg.txt", namen)
		api.herstel(bestand=resultaat["bestand"])
		namen = [b["naam"] for b in api.bladeren(ruimte=self.ruimte)["bestanden"]]
		self.assertIn("weg.txt", namen)

	def test_wijzigingenlijst_met_cursor(self):
		begin = api.wijzigingen(ruimte=self.ruimte, sinds=0)
		upload(self.ruimte, "a.txt", b"a")
		na_upload = api.wijzigingen(ruimte=self.ruimte, sinds=begin["cursor"])
		self.assertEqual([w["soort"] for w in na_upload["wijzigingen"]], ["nieuw"])
		self.assertGreater(na_upload["cursor"], begin["cursor"])
		self.assertEqual(api.wijzigingen(ruimte=self.ruimte, sinds=na_upload["cursor"])["wijzigingen"], [])

	def test_quotum_weigert_te_grote_upload(self):
		frappe.local.conf["openaec_storage_quotum_gb"] = 0.000001  # ~1 kB
		with self.assertRaises(frappe.ValidationError):
			api.upload_start(ruimte=self.ruimte, naam="groot.bin", grootte=10_000)

	def test_quotum_telt_alle_versies(self):
		upload(self.ruimte, "q.txt", b"12345")
		upload(self.ruimte, "q.txt", b"123", basis_versie=1)
		self.assertEqual(api.quotum()["gebruikt_bytes"], 8)
