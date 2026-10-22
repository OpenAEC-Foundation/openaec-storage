import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request
from frappe.website.serve import get_response_content

from openaec_storage.tests.hulp import gebruiker, met_rol, ruim_op
from openaec_storage.www import opslag

VRAAG_BEHEERDER = "Je bent nog geen lid van een ruimte. Vraag je beheerder om je toe te voegen."


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


class TestOpslagpagina(IntegrationTestCase):
	def setUp(self):
		ruim_op()

	def tearDown(self):
		ruim_op()

	def test_gast_gaat_naar_inloggen(self):
		frappe.set_user("Guest")
		set_request(method="GET", path="/opslag")
		response = frappe.website.serve.get_response("opslag")
		self.assertIn(response.status_code, (301, 302, 303, 307, 308))
		self.assertIn("/login?redirect-to=/opslag", response.headers.get("Location", ""))

	def test_ingelogd_toont_de_bestandenpagina(self):
		frappe.set_user("Administrator")
		html = pagina("opslag")
		self.assertIn("oas-app", html)
		self.assertIn("openaec_storage.api.", html)
		self.assertIn('data-csrf="', html)

	# Lege toestand (nog geen enkele ruimte): wat er staat, bepaalt de server met dezelfde regel als het aanmaken.

	def test_lege_toestand_voor_een_beheerder_van_de_omgeving_is_een_knop_naar_beheer(self):
		for rol in ("OpenAEC Eigenaar", "OpenAEC Beheerder"):
			with self.subTest(rol=rol):
				frappe.set_user(met_rol(gebruiker("beheer-" + rol.split()[-1].lower()), rol))
				html = pagina("opslag")
				self.assertIn('href="/beheer/opslag"', html)
				self.assertIn("Ruimte aanmaken in Beheer", html)
				self.assertNotIn(VRAAG_BEHEERDER, html)
				frappe.set_user("Administrator")

	def test_lege_toestand_voor_een_lid_vraagt_om_de_beheerder(self):
		frappe.set_user(gebruiker("lid"))
		html = pagina("opslag")
		self.assertIn(VRAAG_BEHEERDER, html)
		self.assertNotIn("Ruimte aanmaken in Beheer", html)
		self.assertNotIn('href="/beheer/opslag"', html)

	def test_vlag_ruimte_aanmaken_komt_uit_de_paginacontext(self):
		for wie, verwacht in (
			(met_rol(gebruiker("eigenaar"), "OpenAEC Eigenaar"), True),
			(met_rol(gebruiker("systeembeheer"), "System Manager"), True),
			(gebruiker("lid"), False),
		):
			with self.subTest(wie=wie):
				frappe.set_user(wie)
				context = frappe._dict()
				opslag.get_context(context)
				self.assertIs(context.mag_ruimte_maken, verwacht)
				frappe.set_user("Administrator")
