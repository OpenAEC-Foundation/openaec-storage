import frappe
import requests
from frappe.tests import IntegrationTestCase

from openaec_storage import api
from openaec_storage.tests.hulp import NagebootsteS3, foutcode, gebruiker, ruim_op, ruimte, upload

KB = 1000


def namen(ruimte_naam, map_naam=None):
	inhoud = api.bladeren(ruimte=ruimte_naam, map=map_naam)
	return sorted(m["naam"] for m in inhoud["mappen"]), sorted(b["naam"] for b in inhoud["bestanden"])


def inhoud_van(bestand: str) -> bytes:
	return requests.get(api.download(bestand=bestand)["url"], timeout=30).content


def map_id(ruimte_naam, naam, bovenliggende=None):
	return frappe.db.get_value(
		"Opslagmap",
		{"ruimte": ruimte_naam, "naam": naam, "bovenliggende": bovenliggende or ("is", "not set"), "verwijderd_op": ("is", "not set")},
		"name",
	)


def bestand_id(ruimte_naam, naam, map_naam=None):
	return frappe.db.get_value(
		"Opslagbestand",
		{"ruimte": ruimte_naam, "naam": naam, "map": map_naam or ("is", "not set"), "verwijderd_op": ("is", "not set")},
		"name",
	)


class TestKopieren(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		self.s3 = NagebootsteS3().start()
		self.schrijver = gebruiker("schrijver")
		self.lezer = gebruiker("lezer")
		self.buiten = gebruiker("buiten")
		self.a = ruimte("Project A", {self.schrijver: "schrijver", self.lezer: "lezer"})
		self.b = ruimte("Project B", {self.schrijver: "schrijver", self.lezer: "schrijver"})
		frappe.set_user(self.schrijver)
		self.tekeningen = api.maak_map(ruimte=self.a, naam="Tekeningen")["map"]

	def tearDown(self):
		frappe.set_user("Administrator")
		self.s3.stop()
		ruim_op()

	def test_een_bestand_kopieren_naar_een_andere_map(self):
		b = upload(self.a, "plan.pdf", b"inhoud van het plan")
		uit = api.kopieer(bestanden=[b["bestand"]], doel_ruimte=self.a, doel_map=self.tekeningen)
		[r] = uit["resultaten"]
		self.assertTrue(r["ok"])
		self.assertEqual((r["soort"], r["bron"], r["naam"]), ("bestand", b["bestand"], "plan.pdf"))
		self.assertNotEqual(r["bestand"], b["bestand"])
		self.assertEqual(r["versie"], 1)
		# Het origineel blijft staan; de kopie heeft dezelfde inhoud, in een eigen object in de opslag.
		self.assertEqual(namen(self.a), (["Tekeningen"], ["plan.pdf"]))
		self.assertEqual(namen(self.a, self.tekeningen), ([], ["plan.pdf"]))
		self.assertEqual(inhoud_van(r["bestand"]), b"inhoud van het plan")
		sleutels = frappe.get_all("Bestandsversie", filters={"bestand": ("in", [b["bestand"], r["bestand"]])}, pluck="s3_sleutel")
		self.assertEqual(len(set(sleutels)), 2)
		soorten = [(w["soort"], w["naam"]) for w in api.wijzigingen(ruimte=self.a, sinds=0)["wijzigingen"]]
		self.assertEqual(soorten.count(("nieuw", "plan.pdf")), 2)

	def test_dezelfde_naam_op_het_doel_krijgt_een_nummer(self):
		b = upload(self.a, "plan.pdf", b"x")
		eerste = api.kopieer(bestanden=[b["bestand"]], doel_ruimte=self.a)["resultaten"][0]
		tweede = api.kopieer(bestanden=[b["bestand"]], doel_ruimte=self.a)["resultaten"][0]
		self.assertEqual((eerste["naam"], tweede["naam"]), ("plan (2).pdf", "plan (3).pdf"))
		self.assertEqual(namen(self.a), (["Tekeningen"], ["plan (2).pdf", "plan (3).pdf", "plan.pdf"]))

	def test_een_map_met_dezelfde_naam_telt_ook(self):
		b = upload(self.a, "Tekeningen", b"x", map_naam=self.tekeningen)
		r = api.kopieer(bestanden=[b["bestand"]], doel_ruimte=self.a)["resultaten"][0]
		self.assertEqual(r["naam"], "Tekeningen (2)")

	def test_naar_een_andere_ruimte(self):
		b = upload(self.a, "rapport.docx", b"rapport")
		r = api.kopieer(bestanden=[b["bestand"]], doel_ruimte=self.b)["resultaten"][0]
		self.assertTrue(r["ok"])
		self.assertEqual(frappe.db.get_value("Opslagbestand", r["bestand"], "ruimte"), self.b)
		self.assertEqual(namen(self.b), ([], ["rapport.docx"]))
		self.assertEqual(inhoud_van(r["bestand"]), b"rapport")

	def test_een_lezer_mag_naar_een_plek_kopieren_waar_hij_schrijft(self):
		b = upload(self.a, "rapport.docx", b"rapport")
		frappe.set_user(self.lezer)
		r = api.kopieer(bestanden=[b["bestand"]], doel_ruimte=self.b)["resultaten"][0]
		self.assertTrue(r["ok"])
		self.assertEqual(namen(self.b), ([], ["rapport.docx"]))

	def test_zonder_schrijfrecht_op_het_doel_wordt_alles_geweigerd(self):
		b = upload(self.a, "rapport.docx", b"rapport")
		frappe.set_user(self.lezer)
		with self.assertRaises(frappe.PermissionError):
			api.kopieer(bestanden=[b["bestand"]], doel_ruimte=self.a, doel_map=self.tekeningen)
		frappe.set_user(self.schrijver)
		self.assertEqual(namen(self.a, self.tekeningen), ([], []))

	def test_zonder_leesrecht_op_de_bron_niet_die_wel_de_rest(self):
		eigen = upload(self.a, "eigen.txt", b"eigen")
		frappe.set_user("Administrator")
		c = ruimte("Project C", {self.buiten: "schrijver", self.schrijver: "schrijver"})
		frappe.set_user(self.schrijver)
		vreemd = upload(c, "vreemd.txt", b"vreemd")
		# Lezer van A en schrijver van B, maar geen lid van C.
		frappe.set_user(self.lezer)
		uit = api.kopieer(bestanden=[vreemd["bestand"], eigen["bestand"]], doel_ruimte=self.b)["resultaten"]
		self.assertEqual([r["ok"] for r in uit], [False, True])
		self.assertEqual(uit[0]["bron"], vreemd["bestand"])
		self.assertEqual(namen(self.b), ([], ["eigen.txt"]))

	def test_een_bestand_in_de_prullenbak_kopieer_je_niet(self):
		b = upload(self.a, "weg.txt", b"x")
		api.verwijder(bestand=b["bestand"])
		[r] = api.kopieer(bestanden=[b["bestand"]], doel_ruimte=self.b)["resultaten"]
		self.assertFalse(r["ok"])
		self.assertEqual(namen(self.b), ([], []))

	def test_een_map_met_alles_erin(self):
		details = api.maak_map(ruimte=self.a, naam="Details", bovenliggende=self.tekeningen)["map"]
		api.maak_map(ruimte=self.a, naam="Leeg", bovenliggende=details)
		upload(self.a, "gevel.dwg", b"gevel", map_naam=self.tekeningen)
		upload(self.a, "knoop.dwg", b"knoop", map_naam=details)
		weg = upload(self.a, "oud.dwg", b"oud", map_naam=details)
		api.verwijder(bestand=weg["bestand"])
		uit = api.kopieer(mappen=[self.tekeningen], doel_ruimte=self.b)["resultaten"]
		self.assertEqual(len(uit), 1)
		self.assertTrue(uit[0]["ok"], uit[0])
		self.assertEqual((uit[0]["soort"], uit[0]["naam"], uit[0]["bestanden"]), ("map", "Tekeningen", 2))
		kopie = uit[0]["map"]
		self.assertEqual(namen(self.b), (["Tekeningen"], []))
		self.assertEqual(namen(self.b, kopie), (["Details"], ["gevel.dwg"]))
		kopie_details = map_id(self.b, "Details", kopie)
		self.assertEqual(namen(self.b, kopie_details), (["Leeg"], ["knoop.dwg"]))
		self.assertEqual(inhoud_van(bestand_id(self.b, "knoop.dwg", kopie_details)), b"knoop")
		# Het origineel is onveranderd.
		self.assertEqual(namen(self.a, details), (["Leeg"], ["knoop.dwg"]))

	def test_een_map_in_zijn_eigen_map_wordt_een_kopie_met_nummer(self):
		upload(self.a, "gevel.dwg", b"gevel", map_naam=self.tekeningen)
		r = api.kopieer(mappen=[self.tekeningen], doel_ruimte=self.a)["resultaten"][0]
		self.assertTrue(r["ok"])
		self.assertEqual(r["naam"], "Tekeningen (2)")
		self.assertEqual(namen(self.a, r["map"]), ([], ["gevel.dwg"]))

	def test_een_map_niet_in_zichzelf_of_een_eigen_submap(self):
		details = api.maak_map(ruimte=self.a, naam="Details", bovenliggende=self.tekeningen)["map"]
		for doel in (self.tekeningen, details):
			[r] = api.kopieer(mappen=[self.tekeningen], doel_ruimte=self.a, doel_map=doel)["resultaten"]
			self.assertFalse(r["ok"])
			self.assertEqual(r["foutcode"], "kopie_in_zichzelf")
		self.assertEqual(namen(self.a, details), ([], []))

	def test_quotum_van_het_doel(self):
		klein = upload(self.a, "klein.bin", b"x" * 2 * KB)
		groot = upload(self.a, "groot.bin", b"x" * 6 * KB)
		frappe.db.set_value("Ruimte", self.b, "quotum_gb", 5 * KB / 1_000_000_000)
		uit = api.kopieer(bestanden=[groot["bestand"], klein["bestand"]], doel_ruimte=self.b)["resultaten"]
		self.assertEqual([r["ok"] for r in uit], [False, True])
		self.assertEqual(uit[0]["foutcode"], "quotum_ruimte_vol")
		self.assertEqual(namen(self.b), ([], ["klein.bin"]))

	def test_een_mislukte_map_laat_niets_achter(self):
		upload(self.a, "gevel.dwg", b"x" * 6 * KB, map_naam=self.tekeningen)
		frappe.db.set_value("Ruimte", self.b, "quotum_gb", 5 * KB / 1_000_000_000)
		[r] = api.kopieer(mappen=[self.tekeningen], doel_ruimte=self.b)["resultaten"]
		self.assertFalse(r["ok"])
		self.assertEqual(r["foutcode"], "quotum_ruimte_vol")
		self.assertEqual(namen(self.b), ([], []))

	def test_onbekende_ruimte(self):
		self.assertIsNone(foutcode(self, frappe.DoesNotExistError, api.kopieer, bestanden=[], doel_ruimte="bestaat-niet"))

	def test_de_mogelijkheden_noemen_kopieren(self):
		self.assertTrue(api.mogelijkheden()["kopieer"])
