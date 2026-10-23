import frappe
import requests
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from openaec_storage import api, delen
from openaec_storage.tests.hulp import NagebootsteS3, foutcode, gebruiker, ruim_op, ruimte, upload


class TestVergrendelen(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagvergrendeling")
		self.s3 = NagebootsteS3().start()
		self.anna = gebruiker("anna")
		self.bob = gebruiker("bob")
		self.beheer = gebruiker("beheer")
		self.ruimte = ruimte("Project C", {self.anna: "schrijver", self.bob: "schrijver", self.beheer: "beheerder"})
		frappe.set_user(self.anna)
		self.bestand = upload(self.ruimte, "model.ifc", b"v1")["bestand"]

	def tearDown(self):
		self.s3.stop()
		frappe.db.delete("Opslagvergrendeling")
		ruim_op()

	def test_vergrendelen_toont_wie_en_tot_wanneer(self):
		slot = api.vergrendel(bestand=self.bestand, apparaat="werkplek-anna")
		self.assertEqual(slot["gebruiker"], self.anna)
		self.assertIn("verloopt", slot)
		self.assertEqual(api.info(bestand=self.bestand)["vergrendeling"]["gebruiker"], self.anna)

	def test_ander_kan_niet_vergrendelen_zolang_slot_geldt(self):
		api.vergrendel(bestand=self.bestand)
		frappe.set_user(self.bob)
		with self.assertRaises(frappe.ValidationError):
			api.vergrendel(bestand=self.bestand)

	def test_ander_kan_vergrendeld_bestand_niet_verwijderen(self):
		api.vergrendel(bestand=self.bestand)
		frappe.set_user(self.bob)
		code = foutcode(self, frappe.PermissionError, api.verwijder, bestand=self.bestand)
		self.assertEqual(code, "bestand_vergrendeld")
		self.assertFalse(frappe.db.get_value("Opslagbestand", self.bestand, "verwijderd_op"))
		frappe.set_user(self.anna)
		api.verwijder(bestand=self.bestand)
		self.assertTrue(frappe.db.get_value("Opslagbestand", self.bestand, "verwijderd_op"))

	def test_ander_kan_map_met_vergrendeld_bestand_niet_verwijderen(self):
		map = api.maak_map(ruimte=self.ruimte, naam="Modellen")["map"]
		bestand = upload(self.ruimte, "gevel.ifc", b"v1", map_naam=map)["bestand"]
		api.vergrendel(bestand=bestand)
		frappe.set_user(self.bob)
		code = foutcode(self, frappe.PermissionError, api.verwijder_map, map=map)
		self.assertEqual(code, "bestand_vergrendeld")
		self.assertFalse(frappe.db.get_value("Opslagmap", map, "verwijderd_op"))
		self.assertFalse(frappe.db.get_value("Opslagbestand", bestand, "verwijderd_op"))

	def test_ander_kan_vergrendeld_bestand_niet_verplaatsen(self):
		api.vergrendel(bestand=self.bestand)
		frappe.set_user(self.bob)
		code = foutcode(self, frappe.PermissionError, api.verplaats, bestand=self.bestand, naam="ander.ifc")
		self.assertEqual(code, "bestand_vergrendeld")

	def test_upload_door_ander_tijdens_slot_wordt_conflictkopie(self):
		api.vergrendel(bestand=self.bestand)
		frappe.set_user(self.bob)
		resultaat = upload(self.ruimte, "model.ifc", b"bob", basis_versie=1)
		self.assertTrue(resultaat["conflict"])

	def test_houder_van_slot_mag_gewoon_opslaan(self):
		api.vergrendel(bestand=self.bestand)
		resultaat = upload(self.ruimte, "model.ifc", b"v2", basis_versie=1)
		self.assertFalse(resultaat["conflict"])
		self.assertEqual(resultaat["versie"], 2)

	def test_verlopen_slot_telt_niet(self):
		api.vergrendel(bestand=self.bestand)
		frappe.db.set_value("Opslagvergrendeling", {"bestand": self.bestand}, "verloopt", add_to_date(now_datetime(), hours=-1))
		frappe.set_user(self.bob)
		resultaat = upload(self.ruimte, "model.ifc", b"bob", basis_versie=1)
		self.assertFalse(resultaat["conflict"])

	def test_ontgrendelen_door_houder(self):
		api.vergrendel(bestand=self.bestand)
		api.ontgrendel(bestand=self.bestand)
		self.assertIsNone(api.info(bestand=self.bestand)["vergrendeling"])

	def test_alleen_beheerder_mag_slot_openbreken(self):
		api.vergrendel(bestand=self.bestand)
		frappe.set_user(self.bob)
		with self.assertRaises(frappe.PermissionError):
			api.forceer_ontgrendel(bestand=self.bestand)
		frappe.set_user(self.beheer)
		api.forceer_ontgrendel(bestand=self.bestand)
		self.assertIsNone(api.info(bestand=self.bestand)["vergrendeling"])
		soorten = [w["soort"] for w in api.wijzigingen(ruimte=self.ruimte, sinds=0)["wijzigingen"]]
		self.assertIn("ontgrendeld", soorten)


class TestDelen(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagdeling")
		self.s3 = NagebootsteS3().start()
		self.eigenaar = gebruiker("eigenaar")
		self.extern = gebruiker("extern")
		self.ruimte = ruimte("Project D", {self.eigenaar: "beheerder"})
		frappe.set_user(self.eigenaar)
		self.map = frappe.get_doc({"doctype": "Opslagmap", "ruimte": self.ruimte, "naam": "Voor opdrachtgever"}).insert()
		self.sub = frappe.get_doc(
			{"doctype": "Opslagmap", "ruimte": self.ruimte, "naam": "Tekeningen", "bovenliggende": self.map.name}
		).insert()
		self.bestand = upload(self.ruimte, "plattegrond.pdf", b"PDF", map_naam=self.sub.name)["bestand"]

	def tearDown(self):
		self.s3.stop()
		frappe.db.delete("Opslagdeling")
		ruim_op()

	def test_deellink_zonder_account_geeft_download(self):
		deling = api.deel(doel_doctype="Opslagbestand", doel=self.bestand, soort="link")
		frappe.set_user("Guest")
		inhoud = delen.openen(token=deling["token"])
		self.assertEqual(inhoud["bestand"]["naam"], "plattegrond.pdf")
		url = delen.download(token=deling["token"])["url"]
		self.assertEqual(requests.get(url, timeout=30).content, b"PDF")

	def test_deellink_geeft_het_volledige_publieke_adres(self):
		deling = api.deel(doel_doctype="Opslagbestand", doel=self.bestand, soort="link")
		self.assertTrue(deling["url"].endswith(f"/delen/{deling['token']}"))
		self.assertTrue(deling["url"].startswith("http"))

	def test_deellink_met_wachtwoord(self):
		deling = api.deel(doel_doctype="Opslagbestand", doel=self.bestand, soort="link", wachtwoord="geheim123")
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			delen.openen(token=deling["token"])
		self.assertIn("bestand", delen.openen(token=deling["token"], wachtwoord="geheim123"))

	def test_verlopen_of_ingetrokken_link_werkt_niet(self):
		deling = api.deel(doel_doctype="Opslagbestand", doel=self.bestand, soort="link")
		api.trek_in(deling=deling["deling"])
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			delen.openen(token=deling["token"])

	def test_gedeelde_map_geeft_toegang_tot_onderliggende_bestanden(self):
		api.deel(doel_doctype="Opslagmap", doel=self.map.name, soort="gebruiker", gebruiker=self.extern, rechten="lezen")
		frappe.set_user(self.extern)
		self.assertIn("url", api.download(bestand=self.bestand))
		inhoud = api.bladeren(ruimte=self.ruimte, map=self.sub.name)
		self.assertEqual([b["naam"] for b in inhoud["bestanden"]], ["plattegrond.pdf"])

	def test_deling_lezen_geeft_geen_schrijfrecht(self):
		api.deel(doel_doctype="Opslagmap", doel=self.map.name, soort="gebruiker", gebruiker=self.extern, rechten="lezen")
		frappe.set_user(self.extern)
		with self.assertRaises(frappe.PermissionError):
			api.upload_start(ruimte=self.ruimte, naam="x.txt", grootte=1, map=self.sub.name)

	def test_deling_schrijven_geeft_uploadrecht_in_die_map(self):
		api.deel(doel_doctype="Opslagmap", doel=self.map.name, soort="gebruiker", gebruiker=self.extern, rechten="schrijven")
		frappe.set_user(self.extern)
		resultaat = upload(self.ruimte, "opmerkingen.txt", b"ok", map_naam=self.sub.name)
		self.assertEqual(resultaat["versie"], 1)

	def test_zonder_deling_geen_toegang(self):
		frappe.set_user(self.extern)
		with self.assertRaises(frappe.PermissionError):
			api.download(bestand=self.bestand)

	def test_alleen_schrijvers_mogen_delen(self):
		lezer = gebruiker("alleenlezer")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.append("leden", {"gebruiker": lezer, "rol": "lezer"})
		doc.save()
		frappe.set_user(lezer)
		with self.assertRaises(frappe.PermissionError):
			api.deel(doel_doctype="Opslagbestand", doel=self.bestand, soort="link")
