from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from openaec_storage import api, delen
from openaec_storage.tests.hulp import NagebootsteS3, gebruiker, ruim_op, ruimte, upload
from openaec_storage.tests.test_deelpagina import pagina


class TestDeelpaginaStand(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagdeling")
		self.s3 = NagebootsteS3().start()
		self.anna = gebruiker("anna")
		self.ruimte = ruimte("Project S", {self.anna: "schrijver"})
		frappe.set_user(self.anna)
		self.pdf = upload(self.ruimte, "tekening.pdf", b"%PDF-1.7")["bestand"]
		self.link = api.deel(doel_doctype="Opslagbestand", doel=self.pdf)
		frappe.set_user("Guest")
		frappe.local.openaec_status = None

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.openaec_status = None
		self.s3.stop()
		frappe.db.delete("Opslagdeling")
		ruim_op()

	def test_onbekende_link_geeft_een_duidelijke_melding_en_404(self):
		html = pagina("delen/bestaat-niet")
		self.assertIn("Deze link werkt niet (meer)", html)
		self.assertIn("Vraag de afzender om een nieuwe link", html)
		self.assertNotIn("Ongeldig", html)
		self.assertEqual(frappe.local.openaec_status, 404)

	def test_ingetrokken_link_telt_als_onbekend(self):
		frappe.set_user(self.anna)
		api.trek_in(deling=self.link["deling"])
		frappe.set_user("Guest")
		self.assertEqual(delen.stand(self.link["token"])[0], "onbekend")

	def test_verlopen_link_geeft_410_met_de_datum(self):
		frappe.db.set_value("Opslagdeling", self.link["deling"], "verloopt", add_to_date(now_datetime(), days=-1))
		html = pagina(f"delen/{self.link['token']}")
		self.assertIn("Deze link is verlopen", html)
		self.assertEqual(frappe.local.openaec_status, 410)

	def test_verwijderd_bestand_geeft_410(self):
		frappe.set_user(self.anna)
		api.verwijder(bestand=self.pdf)
		frappe.set_user("Guest")
		self.assertEqual(delen.stand(self.link["token"])[0], "weg")

	def test_geldige_link_toont_wie_deelde_en_geen_foutcode(self):
		html = pagina(f"delen/{self.link['token']}")
		self.assertIn('data-stand="geldig"', html)
		self.assertIn("Gedeeld door anna", html)
		self.assertIsNone(frappe.local.openaec_status)

	def test_beveiligde_link_vraagt_eerst_het_wachtwoord(self):
		frappe.set_user(self.anna)
		link = api.deel(doel_doctype="Opslagbestand", doel=self.pdf, wachtwoord="geheim123")
		frappe.set_user("Guest")
		self.assertEqual(delen.stand(link["token"])[0], "wachtwoord")

	def test_pdf_heeft_een_voorbeeld_dat_de_browser_toont(self):
		inhoud = delen.openen(token=self.link["token"])
		self.assertTrue(inhoud["bestand"]["voorbeeld"])
		self.assertIn("gewijzigd", inhoud["bestand"])
		params = parse_qs(urlsplit(delen.download(token=self.link["token"], voorbeeld=1)["url"]).query)
		self.assertTrue(params["response-content-disposition"][0].startswith("inline"))
		self.assertEqual(params["response-content-type"][0], "application/pdf")
		gewoon = parse_qs(urlsplit(delen.download(token=self.link["token"])["url"]).query)
		self.assertTrue(gewoon["response-content-disposition"][0].startswith("attachment"))

	def test_hook_zet_de_gekozen_statuscode(self):
		antwoord = SimpleNamespace(status_code=200)
		frappe.local.openaec_status = 410
		delen.na_verzoek(response=antwoord)
		self.assertEqual(antwoord.status_code, 410)
