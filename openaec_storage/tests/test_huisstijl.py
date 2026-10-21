from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage import huisstijl
from openaec_storage.tests.test_deelpagina import pagina

MERK = {"naam": "Bouwbedrijf De Vries", "logo": "/files/logo.png", "afbeelding": "https://cdn.example.org/bouw.jpg", "kleur": "#1a6e3c"}


def bron():
	return MERK


class TestHuisstijl(IntegrationTestCase):
	def setUp(self):
		self.ws = frappe.get_doc("Website Settings")
		self.oud = (self.ws.app_name, self.ws.app_logo, self.ws.banner_image)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.ws.app_name, self.ws.app_logo, self.ws.banner_image = self.oud
		self.ws.save(ignore_permissions=True)
		frappe.clear_cache()

	def test_zonder_hook_de_website_settings(self):
		self.ws.app_name, self.ws.app_logo, self.ws.banner_image = "Ingenieursbureau Test", "/files/eigen-logo.svg", None
		self.ws.save(ignore_permissions=True)
		frappe.clear_cache()
		with patch("frappe.get_hooks", return_value=[]):
			stijl = huisstijl.voor_deelpagina()
		self.assertEqual(stijl["naam"], "Ingenieursbureau Test")
		self.assertEqual(stijl["logo"], "/files/eigen-logo.svg")
		self.assertIsNone(stijl["kleur"])

	def test_hook_van_het_beheer_gaat_voor(self):
		with patch("frappe.get_hooks", return_value=["openaec_storage.tests.test_huisstijl.bron"]):
			self.assertEqual(huisstijl.voor_deelpagina(), MERK)

	def test_onveilige_waarden_vallen_weg(self):
		stijl = huisstijl._schoon(
			{"naam": "X", "logo": "javascript:alert(1)", "afbeelding": "//elders.example/a.png", "kleur": "red;background:url(x)"}
		)
		self.assertEqual(stijl, {"naam": "X", "logo": None, "afbeelding": None, "kleur": None})

	def test_pagina_rendert_de_huisstijl(self):
		frappe.set_user("Guest")
		with patch.object(huisstijl, "voor_deelpagina", return_value=MERK):
			html = pagina("delen/abc123")
		self.assertIn('src="/files/logo.png"', html)
		self.assertIn("Gedeeld door Bouwbedrijf De Vries", html)
		self.assertIn("--oas-kleur: #1a6e3c", html)
