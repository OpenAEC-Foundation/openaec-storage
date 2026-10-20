import frappe
import requests
from frappe.tests import IntegrationTestCase

from openaec_storage import api, s3
from openaec_storage.tests.hulp import NagebootsteS3, gebruiker, ruim_op, ruimte, upload


class TestPrefix(IntegrationTestCase):
	"""Eén bucket voor meerdere klanten: elke klantsite schrijft onder zijn eigen voorvoegsel."""

	def setUp(self):
		ruim_op()
		self.s3 = NagebootsteS3().start()
		self.lid = gebruiker("prefixlid")
		self.ruimte = ruimte("Prefix", {self.lid: "schrijver"})
		frappe.set_user(self.lid)

	def tearDown(self):
		frappe.local.conf.pop("openaec_storage_prefix", None)
		self.s3.stop()
		ruim_op()

	def test_zonder_voorvoegsel_blijft_het_zoals_het_was(self):
		self.assertTrue(s3.nieuwe_sleutel().startswith("v/"))

	def test_voorvoegsel_komt_voor_elke_nieuwe_sleutel(self):
		frappe.local.conf["openaec_storage_prefix"] = "/klant1.cloud.open-aec.com/"
		self.assertTrue(s3.nieuwe_sleutel().startswith("klant1.cloud.open-aec.com/v/"))
		b = upload(self.ruimte, "a.txt", b"inhoud")
		sleutel = frappe.db.get_value("Bestandsversie", {"bestand": b["bestand"]}, "s3_sleutel")
		self.assertTrue(sleutel.startswith("klant1.cloud.open-aec.com/v/"))
		url = api.download(bestand=b["bestand"])["url"]
		self.assertEqual(requests.get(url, timeout=30).content, b"inhoud")

	def test_bestaande_versies_blijven_leesbaar_na_het_zetten_van_een_voorvoegsel(self):
		b = upload(self.ruimte, "oud.txt", b"van voor")
		frappe.local.conf["openaec_storage_prefix"] = "klant1"
		url = api.download(bestand=b["bestand"])["url"]
		self.assertEqual(requests.get(url, timeout=30).content, b"van voor")
