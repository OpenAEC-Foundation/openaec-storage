import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request
from frappe.website.serve import get_response_content


def pagina(pad: str) -> str:
	set_request(method="GET", path="/" + pad)
	return get_response_content(pad)


class TestDeelpagina(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Guest")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_deellink_opent_de_gastpagina_met_het_token(self):
		html = pagina("delen/abc123")
		self.assertIn('data-token="abc123"', html)
		self.assertIn("openaec_storage.delen.", html)

	def test_token_wordt_ge_escaped(self):
		html = pagina('delen/x"><script>')
		self.assertNotIn('"><script>', html.split("data-token=", 1)[1][:40])

	def test_pagina_kan_ook_bestanden_aanleveren(self):
		html = pagina("delen/abc123")
		self.assertIn("upload_start", html)
		self.assertIn("upload_klaar", html)
