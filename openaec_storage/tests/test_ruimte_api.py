"""Ruimtes aanmaken en bekijken via een eigen methode (voor clients met een token, zoals de desktop-app)."""

import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage import api
from openaec_storage.tests.hulp import gebruiker, met_rol, ruim_op, ruimte


class TestRuimteApi(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		self.beheer = met_rol(gebruiker("ruimtebeheer"), "OpenAEC Beheerder")
		self.lid = gebruiker("ruimtelid")
		self.buiten = gebruiker("buitenstaander")
		self.bestaand = ruimte("Kantoor", {self.lid: "schrijver"})

	def tearDown(self):
		frappe.set_user("Administrator")
		ruim_op()

	def test_beheerder_maakt_een_ruimte_en_wordt_beheerder(self):
		frappe.set_user(self.beheer)
		uit = api.maak_ruimte(naam="  Project Havenkwartier ", soort="project")
		self.assertEqual((uit["naam"], uit["soort"], uit["rol"]), ("Project Havenkwartier", "project", "beheerder"))
		self.assertIn(uit["name"], [r["name"] for r in api.ruimtes()])

	def test_met_mapsjabloon_komen_de_mappen_er_meteen(self):
		frappe.set_user("Administrator")
		sjabloon = frappe.db.get_value("Mapsjabloon", {"soort": "project"}, "name")
		if not sjabloon:
			self.skipTest("geen projectsjabloon in deze omgeving")
		frappe.set_user(self.beheer)
		uit = api.maak_ruimte(naam="Met sjabloon", soort="project", mapsjabloon=sjabloon)
		self.assertTrue(frappe.db.count("Opslagmap", {"ruimte": uit["name"]}))

	def test_alleen_een_beheerder_van_de_omgeving(self):
		frappe.set_user(self.lid)
		with self.assertRaises(frappe.PermissionError):
			api.maak_ruimte(naam="Mag niet", soort="team")

	def test_ongeldige_invoer(self):
		frappe.set_user(self.beheer)
		with self.assertRaises(frappe.ValidationError):
			api.maak_ruimte(naam=" ", soort="team")
		with self.assertRaises(frappe.ValidationError):
			api.maak_ruimte(naam="Ruimte", soort="iets")
		with self.assertRaises(frappe.DoesNotExistError):
			api.maak_ruimte(naam="Ruimte", soort="team", mapsjabloon="Bestaat niet")

	def test_detail_met_leden_voor_leden_en_beheer(self):
		frappe.set_user(self.lid)
		uit = api.ruimte(ruimte=self.bestaand)
		self.assertEqual((uit["naam"], uit["rol"]), ("Kantoor", "schrijver"))
		self.assertIn(self.lid, [lid["gebruiker"] for lid in uit["leden"]])
		frappe.set_user(self.beheer)
		self.assertIsNone(api.ruimte(ruimte=self.bestaand)["rol"])

	def test_geen_detail_voor_een_buitenstaander(self):
		frappe.set_user(self.buiten)
		with self.assertRaises(frappe.PermissionError):
			api.ruimte(ruimte=self.bestaand)
