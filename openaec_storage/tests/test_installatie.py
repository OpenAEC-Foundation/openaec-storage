import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage.installatie import DESKTOP_CLIENT, DESKTOP_REDIRECT, na_migratie, zorg_voor_desktop_client


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
