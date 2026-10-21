import frappe
import requests
from frappe.tests import IntegrationTestCase

from openaec_storage import api, delen
from openaec_storage.tests.hulp import NagebootsteS3, gebruiker, ruim_op, ruimte, upload


def lever_aan(token: str, naam: str, inhoud: bytes, wachtwoord=None, afzender=None) -> dict:
	"""Aanleveren zoals de deelpagina het doet: start, delen naar S3, klaar (als gast)."""
	start = delen.upload_start(token=token, naam=naam, grootte=len(inhoud), wachtwoord=wachtwoord)
	stukken = []
	grootte = start["deelgrootte"]
	for deel in start["delen"]:
		antwoord = requests.put(deel["url"], data=inhoud[(deel["nummer"] - 1) * grootte : deel["nummer"] * grootte], timeout=30)
		antwoord.raise_for_status()
		stukken.append({"nummer": deel["nummer"], "etag": antwoord.headers["ETag"]})
	return delen.upload_klaar(token=token, upload=start["upload"], delen=stukken, wachtwoord=wachtwoord, afzender=afzender)


class TestUploadlink(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagdeling")
		self.s3 = NagebootsteS3().start()
		self.anna = gebruiker("anna")
		self.ruimte = ruimte("Project U", {self.anna: "schrijver"})
		frappe.set_user(self.anna)
		self.map = api.maak_map(ruimte=self.ruimte, naam="Aangeleverd")["map"]
		upload(self.ruimte, "geheim.pdf", b"intern", map_naam=self.map)
		self.link = api.deel(doel_doctype="Opslagmap", doel=self.map, rechten="uploaden")
		frappe.set_user("Guest")

	def tearDown(self):
		frappe.set_user("Administrator")
		self.s3.stop()
		frappe.db.delete("Opslagdeling")
		frappe.db.delete("Notification Log", {"for_user": self.anna})
		ruim_op()

	def bestanden(self) -> dict:
		return {
			r.naam: r
			for r in frappe.get_all(
				"Opslagbestand", filters={"map": self.map}, fields=["name", "naam", "grootte"], ignore_permissions=True
			)
		}

	def test_uploadlink_toont_geen_inhoud_en_geeft_geen_download(self):
		inhoud = delen.openen(token=self.link["token"])
		self.assertTrue(inhoud["uploaden"])
		self.assertEqual(inhoud["map"], "Aangeleverd")
		self.assertNotIn("bestanden", inhoud)
		bestand = self.bestanden()["geheim.pdf"].name
		with self.assertRaises(frappe.PermissionError):
			delen.download(token=self.link["token"], bestand=bestand)

	def test_aanleveren_zet_een_nieuw_bestand_in_de_map(self):
		uitslag = lever_aan(self.link["token"], "tekening.pdf", b"%PDF-1.7 tekening", afzender="Jan Aannemer")
		self.assertEqual(uitslag["naam"], "tekening.pdf")
		rij = self.bestanden()["tekening.pdf"]
		self.assertEqual(rij.grootte, len(b"%PDF-1.7 tekening"))
		versie = frappe.db.get_value("Bestandsversie", {"bestand": rij.name}, ["bron", "maker", "volgnummer"], as_dict=True)
		self.assertEqual((versie.bron, versie.maker, versie.volgnummer), ("link", "Guest", 1))
		self.assertTrue(frappe.db.exists("Opslagwijziging", {"doel": rij.name, "soort": "nieuw"}))
		melding = frappe.db.get_value("Notification Log", {"for_user": self.anna}, "subject")
		self.assertIn("Jan Aannemer", melding)
		self.assertIn("tekening.pdf", melding)

	def test_gelijke_naam_overschrijft_nooit(self):
		uitslag = lever_aan(self.link["token"], "geheim.pdf", b"van buiten")
		self.assertEqual(uitslag["naam"], "geheim (2).pdf")
		self.assertEqual(lever_aan(self.link["token"], "geheim.pdf", b"nog een")["naam"], "geheim (3).pdf")
		self.assertEqual(self.bestanden()["geheim.pdf"].grootte, len(b"intern"))

	def test_leeslink_kan_niet_aanleveren(self):
		frappe.set_user(self.anna)
		leeslink = api.deel(doel_doctype="Opslagmap", doel=self.map)
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			delen.upload_start(token=leeslink["token"], naam="x.txt", grootte=1)

	def test_uploadlink_met_wachtwoord(self):
		frappe.set_user(self.anna)
		link = api.deel(doel_doctype="Opslagmap", doel=self.map, rechten="uploaden", wachtwoord="geheim123")
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			delen.upload_start(token=link["token"], naam="x.txt", grootte=1)
		self.assertEqual(lever_aan(link["token"], "x.txt", b"x", wachtwoord="geheim123")["naam"], "x.txt")

	def test_ingetrokken_uploadlink_werkt_niet(self):
		frappe.set_user(self.anna)
		api.trek_in(deling=self.link["deling"])
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			delen.upload_start(token=self.link["token"], naam="x.txt", grootte=1)

	def test_te_groot_bestand_wordt_geweigerd(self):
		frappe.local.conf["openaec_storage_link_max_gb"] = 0.000001  # ruim 1000 bytes
		try:
			with self.assertRaises(frappe.ValidationError):
				delen.upload_start(token=self.link["token"], naam="groot.bin", grootte=2000)
		finally:
			frappe.local.conf.pop("openaec_storage_link_max_gb", None)

	def test_upload_van_een_andere_link_wordt_geweigerd(self):
		frappe.set_user(self.anna)
		andere = api.deel(doel_doctype="Opslagmap", doel=self.map, rechten="uploaden")
		frappe.set_user("Guest")
		start = delen.upload_start(token=self.link["token"], naam="x.txt", grootte=1)
		with self.assertRaises(frappe.PermissionError):
			delen.upload_klaar(token=andere["token"], upload=start["upload"], delen=[])

	def test_uploadrecht_alleen_voor_een_link_naar_een_map(self):
		frappe.set_user(self.anna)
		bestand = self.bestanden()["geheim.pdf"].name
		with self.assertRaises(frappe.ValidationError):
			api.deel(doel_doctype="Opslagbestand", doel=bestand, rechten="uploaden")
		bob = gebruiker("bob")
		with self.assertRaises(frappe.ValidationError):
			api.deel(doel_doctype="Opslagmap", doel=self.map, soort="gebruiker", gebruiker=bob, rechten="uploaden")


class TestMijnDelingen(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagdeling")
		self.s3 = NagebootsteS3().start()
		self.anna = gebruiker("anna")
		self.bob = gebruiker("bob")
		self.ruimte = ruimte("Project D", {self.anna: "schrijver", self.bob: "schrijver"})
		frappe.set_user(self.anna)
		self.map = api.maak_map(ruimte=self.ruimte, naam="Tekeningen")["map"]
		self.bestand = upload(self.ruimte, "plan.pdf", b"plan")["bestand"]

	def tearDown(self):
		frappe.set_user("Administrator")
		self.s3.stop()
		frappe.db.delete("Opslagdeling")
		ruim_op()

	def test_overzicht_van_eigen_delingen(self):
		api.deel(doel_doctype="Opslagbestand", doel=self.bestand, wachtwoord="geheim123")
		upl = api.deel(doel_doctype="Opslagmap", doel=self.map, rechten="uploaden")
		lijst = api.mijn_delingen()
		self.assertEqual([d["naam"] for d in lijst], ["Tekeningen", "plan.pdf"])
		self.assertEqual(lijst[0]["rechten"], "uploaden")
		self.assertEqual(lijst[0]["url"], upl["url"])
		self.assertEqual(lijst[0]["ruimte_naam"], "Project D")
		self.assertTrue(lijst[1]["beveiligd"])
		self.assertNotIn("wachtwoord_hash", lijst[1])

	def test_alleen_eigen_en_niet_ingetrokken(self):
		eigen = api.deel(doel_doctype="Opslagbestand", doel=self.bestand)
		frappe.set_user(self.bob)
		api.deel(doel_doctype="Opslagmap", doel=self.map)
		self.assertEqual(len(api.mijn_delingen()), 1)
		frappe.set_user(self.anna)
		api.trek_in(deling=eigen["deling"])
		self.assertEqual(api.mijn_delingen(), [])

	def test_delingen_van_verwijderde_bestanden_vallen_weg(self):
		api.deel(doel_doctype="Opslagbestand", doel=self.bestand)
		api.verwijder(bestand=self.bestand)
		self.assertEqual(api.mijn_delingen(), [])
