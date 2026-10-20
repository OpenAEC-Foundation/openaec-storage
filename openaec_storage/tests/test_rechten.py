import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage.rechten import rol_in_ruimte
from openaec_storage.tests.hulp import gebruiker, ruim_op, ruimte


class TestRechten(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		self.beheerder = gebruiker("beheerder")
		self.lezer = gebruiker("lezer")
		self.buitenstaander = gebruiker("buitenstaander")
		self.ruimte = ruimte("Project A", {self.beheerder: "beheerder", self.lezer: "lezer"})

	def tearDown(self):
		ruim_op()

	def test_rol_volgt_uit_lidmaatschap(self):
		self.assertEqual(rol_in_ruimte(self.ruimte, self.beheerder), "beheerder")
		self.assertEqual(rol_in_ruimte(self.ruimte, self.lezer), "lezer")
		self.assertIsNone(rol_in_ruimte(self.ruimte, self.buitenstaander))

	def test_buitenstaander_ziet_ruimte_niet(self):
		frappe.set_user(self.buitenstaander)
		self.assertNotIn(self.ruimte, frappe.get_list("Ruimte", pluck="name"))
		self.assertFalse(frappe.has_permission("Ruimte", "read", self.ruimte))

	def test_lid_ziet_ruimte(self):
		frappe.set_user(self.lezer)
		self.assertIn(self.ruimte, frappe.get_list("Ruimte", pluck="name"))
		self.assertTrue(frappe.has_permission("Ruimte", "read", self.ruimte))

	def test_lezer_mag_ruimte_niet_wijzigen(self):
		frappe.set_user(self.lezer)
		self.assertFalse(frappe.has_permission("Ruimte", "write", self.ruimte))

	def test_beheerder_mag_ruimte_wijzigen(self):
		frappe.set_user(self.beheerder)
		self.assertTrue(frappe.has_permission("Ruimte", "write", self.ruimte))

	def test_dubbele_mapnaam_op_zelfde_plek_geweigerd(self):
		frappe.get_doc({"doctype": "Opslagmap", "ruimte": self.ruimte, "naam": "Tekeningen"}).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Opslagmap", "ruimte": self.ruimte, "naam": "tekeningen"}).insert(ignore_permissions=True)
