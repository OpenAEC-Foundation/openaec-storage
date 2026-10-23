import frappe
import requests
from frappe.tests import IntegrationTestCase

from openaec_storage import api, delen, ruimtebeheer
from openaec_storage.tests.hulp import NagebootsteS3, foutcode, gebruiker, met_rol, ruim_op, ruimte, upload

GEARCHIVEERD = "ruimte_gearchiveerd"


class ArchiefBasis(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagdeling")
		frappe.db.delete("Opslagvergrendeling")
		self.s3 = NagebootsteS3().start()
		self.beheerder = gebruiker("beheerder")
		self.schrijver = gebruiker("schrijver")
		self.lezer = gebruiker("lezer")
		self.omgeving = met_rol(gebruiker("omgeving"), "OpenAEC Beheerder")
		self.ruimte = ruimte(
			"Project Archief", {self.beheerder: "beheerder", self.schrijver: "schrijver", self.lezer: "lezer"}
		)
		frappe.set_user(self.schrijver)
		self.map = api.maak_map(ruimte=self.ruimte, naam="Tekeningen")["map"]
		self.bestand = upload(self.ruimte, "plan.pdf", b"versie een", map_naam=self.map)["bestand"]
		self.link = api.deel(doel_doctype="Opslagbestand", doel=self.bestand)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.s3.stop()
		frappe.db.delete("Opslagdeling")
		frappe.db.delete("Opslagvergrendeling")
		ruim_op()

	def archiveer(self, wie=None):
		frappe.set_user(wie or self.beheerder)
		uitslag = ruimtebeheer.archiveer(ruimte=self.ruimte)
		frappe.set_user(self.schrijver)
		return uitslag


class TestArchiveren(ArchiefBasis):
	def test_beheerder_archiveert_en_het_staat_in_de_wijzigingenlijst(self):
		uitslag = self.archiveer()
		self.assertTrue(uitslag["gearchiveerd"])
		self.assertEqual(uitslag["gearchiveerd_door"], self.beheerder)
		self.assertTrue(uitslag["gearchiveerd_op"])
		soorten = [w["soort"] for w in api.wijzigingen(ruimte=self.ruimte)["wijzigingen"]]
		self.assertEqual(soorten[-1], "gearchiveerd")

	def test_nogmaals_archiveren_verandert_niets(self):
		eerste = self.archiveer()
		self.assertEqual(self.archiveer()["gearchiveerd_op"], eerste["gearchiveerd_op"])
		soorten = [w["soort"] for w in api.wijzigingen(ruimte=self.ruimte)["wijzigingen"]]
		self.assertEqual(soorten.count("gearchiveerd"), 1)

	def test_schrijver_mag_niet_archiveren(self):
		with self.assertRaises(frappe.PermissionError):
			ruimtebeheer.archiveer(ruimte=self.ruimte)
		self.assertFalse(frappe.db.get_value("Ruimte", self.ruimte, "gearchiveerd"))

	def test_beheerder_van_de_omgeving_mag_archiveren_zonder_lid_te_zijn(self):
		self.assertTrue(self.archiveer(self.omgeving)["gearchiveerd"])

	def test_gearchiveerde_ruimte_is_alleen_te_lezen_met_foutcode(self):
		self.archiveer()
		pogingen = [
			(api.upload_start, {"ruimte": self.ruimte, "naam": "nieuw.txt", "grootte": 1}),
			(api.maak_map, {"ruimte": self.ruimte, "naam": "Nieuw"}),
			(api.verwijder, {"bestand": self.bestand}),
			(api.verplaats, {"bestand": self.bestand, "naam": "ander.pdf"}),
			(api.verplaats_map, {"map": self.map, "naam": "Anders"}),
			(api.verwijder_map, {"map": self.map}),
			(api.vergrendel, {"bestand": self.bestand}),
			(api.deel, {"doel_doctype": "Opslagbestand", "doel": self.bestand}),
		]
		for wie in (self.schrijver, self.beheerder, "Administrator"):
			frappe.set_user(wie)
			for functie, argumenten in pogingen:
				with self.subTest(wie=wie, functie=functie.__name__):
					self.assertEqual(foutcode(self, frappe.PermissionError, functie, **argumenten), GEARCHIVEERD)
		frappe.set_user(self.beheerder)
		self.assertEqual(
			foutcode(self, frappe.PermissionError, api.forceer_ontgrendel, bestand=self.bestand), GEARCHIVEERD
		)

	def test_lezen_en_downloaden_blijft_werken(self):
		self.archiveer()
		frappe.set_user(self.lezer)
		self.assertEqual([b["naam"] for b in api.bladeren(ruimte=self.ruimte, map=self.map)["bestanden"]], ["plan.pdf"])
		url = api.download(bestand=self.bestand)["url"]
		self.assertEqual(requests.get(url, timeout=30).content, b"versie een")

	def test_buitenstaander_hoort_niet_dat_de_ruimte_gearchiveerd_is(self):
		self.archiveer()
		frappe.set_user(gebruiker("buitenstaander"))
		self.assertIsNone(
			foutcode(self, frappe.PermissionError, api.upload_start, ruimte=self.ruimte, naam="x.txt", grootte=1)
		)

	def test_niet_in_ruimtes_wel_in_de_beheerlijst(self):
		self.archiveer()
		self.assertNotIn(self.ruimte, [r["name"] for r in api.ruimtes()])
		frappe.set_user(self.beheerder)
		self.assertIn(self.ruimte, [r["name"] for r in ruimtebeheer.lijst()])
		self.assertIn(self.ruimte, [r["name"] for r in ruimtebeheer.lijst(gearchiveerd=1)])
		self.assertNotIn(self.ruimte, [r["name"] for r in ruimtebeheer.lijst(gearchiveerd=0)])

	def test_beheerlijst_alleen_eigen_ruimtes_behalve_voor_de_omgeving(self):
		andere = ruimte("Ander project", {self.schrijver: "beheerder"})
		frappe.set_user(self.beheerder)
		self.assertEqual([r["name"] for r in ruimtebeheer.lijst()], [self.ruimte])
		frappe.set_user(self.omgeving)
		self.assertEqual({r["name"] for r in ruimtebeheer.lijst()}, {self.ruimte, andere})

	def test_terugzetten_maakt_de_ruimte_weer_bruikbaar(self):
		self.archiveer()
		frappe.set_user(self.beheerder)
		uitslag = ruimtebeheer.herstel(ruimte=self.ruimte)
		self.assertFalse(uitslag["gearchiveerd"])
		self.assertIsNone(uitslag["gearchiveerd_op"])
		frappe.set_user(self.schrijver)
		self.assertIn(self.ruimte, [r["name"] for r in api.ruimtes()])
		self.assertEqual(upload(self.ruimte, "nieuw.txt", b"x")["versie"], 1)
		soorten = [w["soort"] for w in api.wijzigingen(ruimte=self.ruimte)["wijzigingen"]]
		self.assertEqual(soorten[-2:], ["teruggezet", "nieuw"])

	def test_archief_niet_te_wijzigen_via_het_formulier(self):
		frappe.set_user(self.beheerder)
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.gearchiveerd = 1
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		self.archiveer()
		frappe.set_user(self.beheerder)
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.gearchiveerd = 0
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_leden_blijven_te_beheren_in_een_gearchiveerde_ruimte(self):
		self.archiveer()
		frappe.set_user(self.beheerder)
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.append("leden", {"gebruiker": gebruiker("nieuw"), "rol": "lezer"})
		doc.save()
		self.assertTrue(frappe.db.get_value("Ruimte", self.ruimte, "gearchiveerd"))

	def test_formulier_geeft_geen_schrijfrecht_op_bestanden(self):
		self.archiveer()
		self.assertTrue(frappe.has_permission("Opslagbestand", "read", self.bestand))
		self.assertFalse(frappe.has_permission("Opslagbestand", "write", self.bestand))


class TestArchiefEnLinks(ArchiefBasis):
	"""Deellinks naar een gearchiveerde ruimte werken niet (ook niet om te lezen); na terugzetten weer wel."""

	def test_deellink_geweigerd_zolang_gearchiveerd(self):
		self.archiveer()
		frappe.set_user("Guest")
		self.assertEqual(foutcode(self, frappe.PermissionError, delen.openen, token=self.link["token"]), GEARCHIVEERD)
		self.assertEqual(foutcode(self, frappe.PermissionError, delen.download, token=self.link["token"]), GEARCHIVEERD)
		self.assertEqual(delen.stand(self.link["token"])[0], "gearchiveerd")
		frappe.set_user(self.beheerder)
		ruimtebeheer.herstel(ruimte=self.ruimte)
		frappe.set_user("Guest")
		self.assertIn("url", delen.download(token=self.link["token"]))

	def test_uploadlink_geweigerd_zolang_gearchiveerd(self):
		uploadlink = api.deel(doel_doctype="Opslagmap", doel=self.map, rechten="uploaden")
		self.archiveer()
		frappe.set_user("Guest")
		self.assertEqual(
			foutcode(
				self, frappe.PermissionError, delen.upload_start, token=uploadlink["token"], naam="a.pdf", grootte=1
			),
			GEARCHIVEERD,
		)


class TestArchiefEnSync(ArchiefBasis):
	"""De desktop-app en de WebDAV-gateway gebruiken dezelfde API: ook wat al liep, wordt geweigerd."""

	def test_lopende_upload_wordt_na_archiveren_niet_afgerond(self):
		start = api.upload_start(
			ruimte=self.ruimte, naam="plan.pdf", grootte=3, map=self.map, basis_versie=1, bron="sync"
		)
		antwoord = requests.put(start["delen"][0]["url"], data=b"abc", timeout=30)
		self.archiveer()
		self.assertEqual(
			foutcode(
				self,
				frappe.PermissionError,
				api.upload_klaar,
				upload=start["upload"],
				delen=[{"nummer": 1, "etag": antwoord.headers["ETag"]}],
			),
			GEARCHIVEERD,
		)

	def test_wijzigingenlijst_blijft_leesbaar(self):
		self.archiveer()
		self.assertTrue(api.wijzigingen(ruimte=self.ruimte, sinds=0)["wijzigingen"])

	def test_opslaan_via_tijdelijk_bestand_en_move_geweigerd(self):
		# Zo slaan WebDAV-programma's op: eerst een tijdelijk bestand, dan MOVE over het origineel.
		tijdelijk = upload(self.ruimte, "plan.pdf.tmp", b"versie twee", map_naam=self.map)["bestand"]
		self.archiveer()
		self.assertEqual(
			foutcode(
				self,
				frappe.PermissionError,
				api.verplaats,
				bestand=tijdelijk,
				naam="plan.pdf",
				map=self.map,
				overschrijf=1,
			),
			GEARCHIVEERD,
		)
