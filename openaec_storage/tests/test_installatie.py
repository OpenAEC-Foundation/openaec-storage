import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage.installatie import (
	DESKTOP_CLIENT,
	DESKTOP_REDIRECT,
	PROJECTVELD,
	na_migratie,
	zorg_voor_desktop_client,
	zorg_voor_projectveld,
)


class TestDesktopClient(IntegrationTestCase):
	def test_desktop_client_is_publiek_met_loopback_redirect(self):
		zorg_voor_desktop_client()
		zorg_voor_desktop_client()  # tweede keer: bijwerken, geen dubbele
		client = frappe.get_doc("OAuth Client", DESKTOP_CLIENT)
		self.assertEqual(client.client_id, DESKTOP_CLIENT)
		self.assertTrue(client.is_public_client())
		self.assertEqual(client.default_redirect_uri, DESKTOP_REDIRECT)
		self.assertEqual([r.role for r in client.allowed_roles], ["All"])
		self.assertEqual(frappe.db.count("OAuth Client", {"app_name": "OpenAEC Cloud (desktop)"}), 1)

	def test_migreren_maakt_de_standaardsjablonen(self):
		na_migratie()
		na_migratie()
		self.assertTrue(frappe.db.exists("Mapsjabloon", "Bedrijf"))
		self.assertTrue(frappe.db.exists("Mapsjabloon", "Project (BIM)"))
		self.assertEqual(frappe.db.count("Mapsjabloon", {"name": "Bedrijf"}), 1)


class TestProjectveld(IntegrationTestCase):
	def test_alleen_met_het_doctype_project(self):
		zorg_voor_projectveld()
		zorg_voor_projectveld()  # tweede keer: niets dubbel
		met_project = bool(frappe.db.exists("DocType", "Project"))
		self.assertEqual(bool(frappe.db.exists("Custom Field", PROJECTVELD)), met_project)
		if met_project:
			veld = frappe.get_meta("Ruimte").get_field("project")
			self.assertEqual((veld.fieldtype, veld.options), ("Link", "Project"))
			self.assertEqual(frappe.db.count("Custom Field", {"dt": "Ruimte", "fieldname": "project"}), 1)
