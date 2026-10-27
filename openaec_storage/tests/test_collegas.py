import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from openaec_storage import api, ruimtebeheer
from openaec_storage.tests import hulp
from openaec_storage.tests.hulp import DOMEIN, NagebootsteS3, foutcode, met_rol, ruim_op, ruimte, upload


def gebruiker(naam: str) -> str:
	"""Een testgebruiker die meetelt als systeemgebruiker (zonder rollen maakt Frappe er een websitegebruiker van)."""
	email = hulp.gebruiker(naam)
	frappe.db.set_value("User", email, "user_type", "System User")
	return email


class TestCollegasEnDelingen(IntegrationTestCase):
	"""Voor de deel-dialoog van de desktop-app: collega's zoeken, een onbekende gebruiker en wie er toegang heeft."""

	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagdeling")
		self.s3 = NagebootsteS3().start()
		self.schrijver = gebruiker("schrijver")
		self.lezer = gebruiker("lezer")
		self.anna = gebruiker("anna")
		self.uit = gebruiker("uitgeschakeld")
		frappe.db.set_value("User", self.uit, "enabled", 0)
		self.web = gebruiker("website")
		frappe.db.set_value("User", self.web, "user_type", "Website User")
		frappe.db.set_value("User", self.anna, "full_name", "Anna de Vries")
		self.omgeving = met_rol(gebruiker("omgeving"), "OpenAEC Beheerder")
		self.ruimte = ruimte("Project Delen", {self.schrijver: "schrijver", self.lezer: "lezer"})
		frappe.set_user(self.schrijver)
		self.bestand = upload(self.ruimte, "plan.pdf", b"pdf")["bestand"]

	def tearDown(self):
		frappe.set_user("Administrator")
		self.s3.stop()
		frappe.db.delete("Opslagdeling")
		ruim_op()

	def zoek(self, tekst="", ruimte=None):
		return api.collegas(tekst=tekst, ruimte=ruimte or self.ruimte)

	def test_actieve_systeemgebruikers_met_hun_rol_in_de_ruimte(self):
		uit = {c["gebruiker"]: c for c in self.zoek(DOMEIN)}
		self.assertEqual(uit[self.lezer]["lid_als"], "lezer")
		self.assertEqual(uit[self.schrijver]["lid_als"], "schrijver")
		self.assertIsNone(uit[self.anna]["lid_als"])
		self.assertEqual(uit[self.anna]["naam"], "Anna de Vries")
		for weg in (self.uit, self.web):
			self.assertNotIn(weg, uit)
		self.assertNotIn("Administrator", [c["gebruiker"] for c in self.zoek("admin")])
		self.assertNotIn("Guest", [c["gebruiker"] for c in self.zoek("guest")])
		self.assertEqual(sorted(self.zoek(DOMEIN)[0]), ["gebruiker", "lid_als", "naam"])

	def test_zoeken_op_naam_en_email_met_een_grens(self):
		self.assertEqual([c["gebruiker"] for c in self.zoek("de vries")], [self.anna])
		self.assertEqual([c["gebruiker"] for c in self.zoek("ANNA@storage")], [self.anna])
		self.assertEqual(self.zoek("geen_treffer%"), [])
		for i in range(25):
			gebruiker(f"veel{i:02}")
		self.assertEqual(len(self.zoek("veel")), 20)

	def test_een_gast_nooit(self):
		"""Een gast (websitegebruiker) ziet de medewerkers niet, ook niet als schrijver van de ruimte."""
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.append("leden", {"gebruiker": self.web, "rol": "schrijver"})
		doc.save()
		frappe.set_user(self.web)
		self.assertEqual(foutcode(self, frappe.PermissionError, self.zoek, "anna"), "alleen_leden")
		frappe.set_user("Guest")
		self.assertEqual(foutcode(self, frappe.PermissionError, self.zoek, "anna"), "alleen_leden")

	def test_alleen_wie_mag_delen(self):
		frappe.set_user(self.lezer)
		with self.assertRaises(frappe.PermissionError):
			self.zoek()
		frappe.set_user(self.anna)
		with self.assertRaises(frappe.PermissionError):
			self.zoek()
		with self.assertRaises(frappe.PermissionError):
			api.collegas(tekst="")
		# Een beheerder van de omgeving mag het altijd, ook zonder ruimte.
		frappe.set_user(self.omgeving)
		self.assertTrue(api.collegas(tekst="anna"))
		self.assertIsNone(api.collegas(tekst="anna")[0]["lid_als"])
		self.assertTrue(self.zoek("anna"))

	def test_gearchiveerde_ruimte(self):
		frappe.set_user("Administrator")
		ruimtebeheer.archiveer(ruimte=self.ruimte)
		frappe.set_user(self.schrijver)
		self.assertEqual(foutcode(self, frappe.PermissionError, self.zoek), "ruimte_gearchiveerd")

	def test_onbekend_adres_wordt_als_gast_uitgenodigd(self):
		"""Met Beheer op de site: een onbekend adres gaat als gast (OpenAEC-account) naar Beheer, dat de gebruiker hier
		aanmaakt; daarna volgt de deling op die gebruiker."""
		from unittest import mock

		nieuw = "extern@elders.example.com"
		aanroepen = []

		def nodig_uit(email, doel_doctype, doel):
			aanroepen.append((email, doel_doctype, doel))
			hulp.gebruiker("extern")  # Beheer maakt de gebruiker vooraf aan (gebruikers.maak_vooraf)
			frappe.db.set_value("User", hulp.gebruiker("extern"), "user_type", "Website User")
			return {"status": "uitgenodigd", "gebruiker": hulp.gebruiker("extern")}

		echt = frappe.get_attr
		with mock.patch.object(
			frappe, "get_attr", side_effect=lambda pad: nodig_uit if pad == api.GAST_UITNODIGEN else echt(pad)
		):
			uit = api.deel(doel_doctype="Opslagbestand", doel=self.bestand, soort="gebruiker", gebruiker=nieuw.upper())
		self.assertEqual(aanroepen, [(nieuw, "Opslagbestand", self.bestand)])
		self.assertEqual((uit["uitnodiging"], uit["gebruiker"]), ("uitgenodigd", hulp.gebruiker("extern")))
		self.assertEqual(frappe.db.get_value("Opslagdeling", uit["deling"], "gebruiker"), hulp.gebruiker("extern"))

	def test_onbekende_gebruiker_geeft_een_foutcode(self):
		"""Zonder Beheer op de site (geen gastuitnodiging) blijft een onbekend adres een fout met een vaste code."""
		from unittest import mock

		echt = frappe.get_attr

		def zonder_beheer(pad):
			if pad == api.GAST_UITNODIGEN:
				raise AttributeError(pad)
			return echt(pad)

		with mock.patch.object(frappe, "get_attr", side_effect=zonder_beheer):
			code = foutcode(
				self,
				frappe.ValidationError,
				api.deel,
				doel_doctype="Opslagbestand",
				doel=self.bestand,
				soort="gebruiker",
				gebruiker="niemand@elders.example.com",
			)
		self.assertEqual(code, "gebruiker_onbekend")
		with self.assertRaises(frappe.ValidationError) as fout:
			api.deel(doel_doctype="Opslagbestand", doel=self.bestand, soort="gebruiker", gebruiker="")
		self.assertIn("bestaande gebruiker", str(fout.exception))

	def test_delingen_met_beveiligd_maker_en_verlopen(self):
		api.deel(doel_doctype="Opslagbestand", doel=self.bestand, wachtwoord="geheim")
		verlopen = str(add_to_date(now_datetime(), days=-1))
		api.deel(doel_doctype="Opslagbestand", doel=self.bestand, verloopt=verlopen)
		api.deel(doel_doctype="Opslagbestand", doel=self.bestand, soort="gebruiker", gebruiker=self.anna, rechten="schrijven")
		frappe.db.set_value("User", self.schrijver, "full_name", "Sam Schrijver")
		frappe.set_user(self.lezer)
		lijst = api.delingen(doel_doctype="Opslagbestand", doel=self.bestand)
		self.assertEqual(len(lijst), 3)
		for d in lijst:
			self.assertNotIn("wachtwoord_hash", d)
			self.assertEqual((d["gemaakt_door"], d["gemaakt_door_naam"]), (self.schrijver, "Sam Schrijver"))
		beveiligd = [d for d in lijst if d["beveiligd"]]
		self.assertEqual(len(beveiligd), 1)
		self.assertEqual([d["verlopen"] for d in lijst if d["verloopt"]], [True])
		collega = next(d for d in lijst if d["soort"] == "gebruiker")
		self.assertEqual((collega["gebruiker"], collega["rechten"], collega["verlopen"], collega["beveiligd"]), (self.anna, "schrijven", False, False))
		for veld in ("name", "soort", "gebruiker", "rechten", "verloopt", "token", "creation"):
			self.assertIn(veld, collega)
