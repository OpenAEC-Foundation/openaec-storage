import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage import api, delen, ruimtebeheer
from openaec_storage.tests.hulp import NagebootsteS3, foutcode, gebruiker, met_rol, ruim_op, ruimte, upload

KB = 1000


def zet_quotum(ruimte_naam: str, kilobytes: float) -> None:
	frappe.db.set_value("Ruimte", ruimte_naam, "quotum_gb", kilobytes * KB / 1_000_000_000)


class TestQuotumRuimte(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagdeling")
		self.s3 = NagebootsteS3().start()
		self.schrijver = gebruiker("schrijver")
		self.lezer = gebruiker("lezer")
		self.beheerder = gebruiker("beheerder")
		self.a = ruimte("Project A", {self.schrijver: "schrijver", self.lezer: "lezer", self.beheerder: "beheerder"})
		self.b = ruimte("Project B", {self.schrijver: "schrijver"})
		frappe.set_user(self.schrijver)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.s3.stop()
		frappe.db.delete("Opslagdeling")
		ruim_op()

	def test_zonder_eigen_quotum_geen_limiet(self):
		upload(self.a, "groot.bin", b"x" * 20 * KB)
		self.assertEqual(api.quotum_ruimte(ruimte=self.a), {"gebruikt_bytes": 20 * KB, "limiet_bytes": None})

	def test_ruimte_vol_geeft_foutcode_en_andere_ruimte_gaat_door(self):
		zet_quotum(self.a, 10)
		upload(self.a, "eerste.bin", b"x" * 6 * KB)
		self.assertEqual(
			foutcode(self, frappe.ValidationError, api.upload_start, ruimte=self.a, naam="tweede.bin", grootte=5 * KB),
			"quotum_ruimte_vol",
		)
		# De WebDAV-gateway herkent een volle opslag aan "quotum" in de melding (507 voor WebDAV-programma's).
		with self.assertRaises(frappe.ValidationError) as fout:
			api.upload_start(ruimte=self.a, naam="tweede.bin", grootte=5 * KB)
		self.assertIn("quotum", str(fout.exception))
		upload(self.b, "tweede.bin", b"x" * 5 * KB)

	def test_oude_versies_tellen_mee_net_als_bij_de_pool(self):
		eerste = upload(self.a, "plan.pdf", b"x" * 4 * KB)
		upload(self.a, "plan.pdf", b"y" * 3 * KB, basis_versie=eerste["versie"])
		api.verwijder(bestand=eerste["bestand"])
		self.assertEqual(api.quotum_ruimte(ruimte=self.a)["gebruikt_bytes"], 7 * KB)
		zet_quotum(self.a, 8)
		self.assertEqual(api.quotum_ruimte(ruimte=self.a)["limiet_bytes"], 8 * KB)
		self.assertEqual(
			foutcode(self, frappe.ValidationError, api.upload_start, ruimte=self.a, naam="c.bin", grootte=2 * KB),
			"quotum_ruimte_vol",
		)

	def test_sync_update_met_basisversie_telt_ook(self):
		zet_quotum(self.a, 10)
		eerste = upload(self.a, "model.ifc", b"x" * 6 * KB)
		self.assertEqual(
			foutcode(
				self,
				frappe.ValidationError,
				api.upload_start,
				ruimte=self.a,
				naam="model.ifc",
				grootte=6 * KB,
				basis_versie=eerste["versie"],
				bron="sync",
			),
			"quotum_ruimte_vol",
		)

	def test_pool_vol_geeft_eigen_foutcode(self):
		frappe.local.conf["openaec_storage_quotum_gb"] = 10 * KB / 1_000_000_000
		zet_quotum(self.a, 100)
		self.assertEqual(
			foutcode(self, frappe.ValidationError, api.upload_start, ruimte=self.a, naam="a.bin", grootte=11 * KB),
			"quotum_pool_vol",
		)

	def test_overboeking_mag_wat_het_eerst_vol_is_weigert(self):
		frappe.local.conf["openaec_storage_quotum_gb"] = 10 * KB / 1_000_000_000
		zet_quotum(self.a, 8)
		zet_quotum(self.b, 8)
		upload(self.a, "a.bin", b"x" * 6 * KB)
		self.assertEqual(
			foutcode(self, frappe.ValidationError, api.upload_start, ruimte=self.b, naam="b.bin", grootte=6 * KB),
			"quotum_pool_vol",
		)
		upload(self.b, "b.bin", b"x" * 4 * KB)

	def test_quotum_van_de_omgeving_houdt_zijn_vorm(self):
		upload(self.a, "a.bin", b"x" * KB)
		self.assertEqual(set(api.quotum()), {"gebruikt_bytes", "limiet_bytes", "overdrachten_bytes"})
		self.assertEqual(api.quotum()["gebruikt_bytes"], KB)

	def test_uploadlink_volgt_het_quotum_van_de_ruimte(self):
		zet_quotum(self.a, 1)
		map_naam = api.maak_map(ruimte=self.a, naam="Aanleveren")["map"]
		link = api.deel(doel_doctype="Opslagmap", doel=map_naam, rechten="uploaden")
		frappe.set_user("Guest")
		self.assertEqual(
			foutcode(self, frappe.ValidationError, delen.upload_start, token=link["token"], naam="a.pdf", grootte=2 * KB),
			"quotum_ruimte_vol",
		)

	def test_lezer_ziet_het_quotum_buitenstaander_niet(self):
		frappe.set_user(self.lezer)
		self.assertIn("gebruikt_bytes", api.quotum_ruimte(ruimte=self.a))
		frappe.set_user(gebruiker("buitenstaander"))
		with self.assertRaises(frappe.PermissionError):
			api.quotum_ruimte(ruimte=self.a)

	def test_alleen_de_omgeving_wijzigt_het_quotum(self):
		frappe.set_user(self.beheerder)
		doc = frappe.get_doc("Ruimte", self.a)
		doc.quotum_gb = 50
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		with self.assertRaises(frappe.PermissionError):
			ruimtebeheer.zet_quotum(ruimte=self.a, quotum_gb=50)
		frappe.set_user(met_rol(gebruiker("omgeving"), "OpenAEC Beheerder"))
		self.assertEqual(ruimtebeheer.zet_quotum(ruimte=self.a, quotum_gb="50")["limiet_bytes"], 50_000_000_000)
		self.assertEqual(api.quotum_ruimte(ruimte=self.a)["limiet_bytes"], 50_000_000_000)
		rij = next(r for r in ruimtebeheer.lijst() if r["name"] == self.a)
		self.assertEqual((rij["limiet_bytes"], rij["gebruikt_bytes"]), (50_000_000_000, 0))
		self.assertIsNone(ruimtebeheer.zet_quotum(ruimte=self.a, quotum_gb=0)["limiet_bytes"])
		with self.assertRaises(frappe.ValidationError):
			ruimtebeheer.zet_quotum(ruimte=self.a, quotum_gb=-1)
