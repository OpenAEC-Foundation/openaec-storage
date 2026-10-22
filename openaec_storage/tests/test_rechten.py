import re

import frappe
from frappe.api import v1 as rest
from frappe.client import insert as client_insert
from frappe.client import set_value as client_set_value
from frappe.tests import IntegrationTestCase

from openaec_storage import rechten
from openaec_storage.rechten import rol_in_ruimte
from openaec_storage.tests.hulp import gebruiker, met_rol, ruim_op, ruimte

ALLEEN_BEHEER = re.escape("Alleen een beheerder van de omgeving kan een ruimte aanmaken.")
MAKERS_SLEUTEL = "openaec_storage_ruimte_makers"


def via_rest(handler, *args, data: dict):
	"""Roep een REST-handler van /api/resource aan zoals een aanvraag dat doet: de gegevens komen uit het verzoek."""
	oud = frappe.local.form_dict
	frappe.local.form_dict = frappe._dict(data=frappe.as_json(data))
	try:
		return handler(*args)
	finally:
		frappe.local.form_dict = oud


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


class TestRuimteAanmaken(IntegrationTestCase):
	"""Een nieuwe ruimte aanmaken mag alleen een beheerder van de omgeving; de maker wordt er beheerder van."""

	def setUp(self):
		ruim_op()
		self.oude_makers = frappe.local.conf.pop(MAKERS_SLEUTEL, None)
		self.beheerder = gebruiker("beheerder")
		self.schrijver = gebruiker("schrijver")
		self.lezer = gebruiker("lezer")
		self.gast = gebruiker("gast")
		self.buitenstaander = gebruiker("buitenstaander")
		self.bestaande = ruimte(
			"Project A",
			{self.beheerder: "beheerder", self.schrijver: "schrijver", self.lezer: "lezer", self.gast: "gast"},
		)

	def tearDown(self):
		frappe.local.conf.pop(MAKERS_SLEUTEL, None)
		if self.oude_makers is not None:
			frappe.local.conf[MAKERS_SLEUTEL] = self.oude_makers
		frappe.clear_messages()
		ruim_op()

	def zonder_beheerrol(self):
		# Ook de beheerder van een bestaande ruimte is geen beheerder van de omgeving.
		return (self.beheerder, self.schrijver, self.lezer, self.gast, self.buitenstaander)

	def ruimtes_genaamd(self, naam: str) -> list[str]:
		return frappe.get_all("Ruimte", filters={"naam": naam}, pluck="name", ignore_permissions=True)

	def test_lid_of_gast_kan_geen_ruimte_aanmaken(self):
		for wie in self.zonder_beheerrol():
			with self.subTest(wie=wie):
				frappe.set_user(wie)
				with self.assertRaisesRegex(frappe.PermissionError, ALLEEN_BEHEER):
					frappe.get_doc({"doctype": "Ruimte", "naam": "Eigen ruimte", "soort": "project"}).insert()
				frappe.set_user("Administrator")
				self.assertEqual(self.ruimtes_genaamd("Eigen ruimte"), [])

	def test_lid_kan_geen_ruimte_aanmaken_via_rest(self):
		for wie in self.zonder_beheerrol():
			with self.subTest(wie=wie):
				frappe.set_user(wie)
				with self.assertRaisesRegex(frappe.PermissionError, ALLEEN_BEHEER):
					via_rest(rest.create_doc, "Ruimte", data={"naam": "Via REST", "soort": "team"})
				with self.assertRaisesRegex(frappe.PermissionError, ALLEEN_BEHEER):
					client_insert({"doctype": "Ruimte", "naam": "Via client", "soort": "team"})
				frappe.set_user("Administrator")
				self.assertEqual(self.ruimtes_genaamd("Via REST") + self.ruimtes_genaamd("Via client"), [])

	def test_beheerrol_mag_ruimte_aanmaken_en_wordt_er_beheerder(self):
		for rol in ("OpenAEC Eigenaar", "OpenAEC Beheerder", "System Manager"):
			with self.subTest(rol=rol):
				maker = met_rol(gebruiker("maker-" + rol.lower().replace(" ", "-")), rol)
				frappe.set_user(maker)
				self.assertTrue(rechten.mag_ruimte_maken())
				naam = via_rest(rest.create_doc, "Ruimte", data={"naam": "Ruimte van " + rol, "soort": "team"}).name
				frappe.set_user("Administrator")
				self.assertEqual(rol_in_ruimte(naam, maker), "beheerder")

	def test_administrator_mag_ruimte_aanmaken(self):
		frappe.set_user("Administrator")
		doc = frappe.get_doc({"doctype": "Ruimte", "naam": "Door Administrator", "soort": "team"}).insert()
		self.assertTrue(frappe.db.exists("Ruimte", doc.name))

	def test_standaard_mogen_de_beheerrollen_van_de_omgeving_ruimtes_maken(self):
		self.assertEqual(set(rechten.ruimte_makers()), {"System Manager", "OpenAEC Eigenaar", "OpenAEC Beheerder"})
		self.assertFalse(rechten.mag_ruimte_maken("Guest"))
		self.assertTrue(rechten.mag_ruimte_maken("Administrator"))

	def test_makers_zijn_in_te_stellen_via_site_config(self):
		frappe.local.conf[MAKERS_SLEUTEL] = ["Ruimtemaker Test"]
		eigen_rol = met_rol(gebruiker("ruimtemaker"), "Ruimtemaker Test")
		beheer = met_rol(gebruiker("omgevingsbeheer"), "OpenAEC Beheerder")
		systeem = met_rol(gebruiker("systeembeheer"), "System Manager")

		self.assertTrue(rechten.mag_ruimte_maken(eigen_rol))
		self.assertFalse(rechten.mag_ruimte_maken(beheer))
		# Administrator en System Manager hebben overal toegang, ook als de lijst hen niet noemt.
		self.assertTrue(rechten.mag_ruimte_maken(systeem))
		self.assertTrue(rechten.mag_ruimte_maken("Administrator"))

		frappe.set_user(beheer)
		with self.assertRaisesRegex(frappe.PermissionError, ALLEEN_BEHEER):
			frappe.get_doc({"doctype": "Ruimte", "naam": "Niet toegestaan", "soort": "team"}).insert()
		frappe.set_user(eigen_rol)
		naam = frappe.get_doc({"doctype": "Ruimte", "naam": "Wel toegestaan", "soort": "team"}).insert().name
		self.assertEqual(rol_in_ruimte(naam, eigen_rol), "beheerder")

	def test_makers_als_tekst_in_site_config(self):
		frappe.local.conf[MAKERS_SLEUTEL] = "Ruimtemaker Test, OpenAEC Eigenaar"
		self.assertEqual(rechten.ruimte_makers(), ["Ruimtemaker Test", "OpenAEC Eigenaar"])
		frappe.local.conf[MAKERS_SLEUTEL] = []
		self.assertEqual(rechten.ruimte_makers(), [])
		self.assertFalse(rechten.mag_ruimte_maken(met_rol(gebruiker("eigenaar"), "OpenAEC Eigenaar")))


class TestLedenBeheer(IntegrationTestCase):
	"""Leden en rollen van een bestaande ruimte wijzigen mag alleen een beheerder van die ruimte, langs elke weg."""

	def setUp(self):
		ruim_op()
		self.beheerder = gebruiker("beheerder")
		self.schrijver = gebruiker("schrijver")
		self.lezer = gebruiker("lezer")
		self.gast = gebruiker("gast")
		self.buitenstaander = gebruiker("buitenstaander")
		self.ruimte = ruimte(
			"Project A",
			{self.beheerder: "beheerder", self.schrijver: "schrijver", self.lezer: "lezer", self.gast: "gast"},
		)
		self.begin = self.leden()

	def tearDown(self):
		frappe.clear_messages()
		ruim_op()

	def leden(self) -> list[tuple[str, str]]:
		return sorted(
			frappe.get_all(
				"Ruimtelid",
				filters={"parent": self.ruimte, "parenttype": "Ruimte"},
				fields=["gebruiker", "rol"],
				as_list=True,
				ignore_permissions=True,
			)
		)

	def rij(self, wie: str) -> str:
		return frappe.db.get_value("Ruimtelid", {"parent": self.ruimte, "parenttype": "Ruimte", "gebruiker": wie}, "name")

	def leden_ongewijzigd(self):
		frappe.set_user("Administrator")
		self.assertEqual(self.leden(), self.begin)
		self.assertIsNone(rol_in_ruimte(self.ruimte, self.buitenstaander))

	def test_lid_kan_eigen_rol_niet_verhogen_via_save(self):
		for lid in (self.schrijver, self.lezer, self.gast):
			with self.subTest(lid=lid):
				frappe.set_user(lid)
				doc = frappe.get_doc("Ruimte", self.ruimte)
				for rij in doc.leden:
					if rij.gebruiker == lid:
						rij.rol = "beheerder"
				with self.assertRaises(frappe.PermissionError):
					doc.save()
				self.leden_ongewijzigd()

	def test_lid_kan_niemand_toevoegen_via_save(self):
		for lid in (self.schrijver, self.lezer, self.gast):
			for doel in (lid, self.buitenstaander):
				with self.subTest(lid=lid, doel=doel):
					frappe.set_user(lid)
					doc = frappe.get_doc("Ruimte", self.ruimte)
					doc.append("leden", {"gebruiker": doel, "rol": "beheerder"})
					with self.assertRaises(frappe.PermissionError):
						doc.save()
					self.leden_ongewijzigd()

	def test_lid_kan_niemand_toevoegen_via_client_insert_van_een_ruimtelid(self):
		for lid in (self.schrijver, self.lezer, self.gast):
			for doel in (lid, self.buitenstaander):
				with self.subTest(lid=lid, doel=doel):
					frappe.set_user(lid)
					with self.assertRaises(frappe.PermissionError):
						client_insert(
							{
								"doctype": "Ruimtelid",
								"parenttype": "Ruimte",
								"parent": self.ruimte,
								"parentfield": "leden",
								"gebruiker": doel,
								"rol": "beheerder",
							}
						)
					self.leden_ongewijzigd()

	def test_lid_kan_rol_niet_verhogen_via_set_value(self):
		for lid in (self.schrijver, self.lezer, self.gast):
			with self.subTest(lid=lid):
				frappe.set_user(lid)
				with self.assertRaises(frappe.PermissionError):
					client_set_value("Ruimtelid", self.rij(lid), "rol", "beheerder")
				with self.assertRaises(frappe.PermissionError):
					client_set_value("Ruimte", self.ruimte, "leden", [{"gebruiker": lid, "rol": "beheerder"}])
				self.leden_ongewijzigd()

	def test_lid_kan_leden_niet_wijzigen_via_rest(self):
		for lid in (self.schrijver, self.lezer, self.gast):
			with self.subTest(lid=lid):
				frappe.set_user(lid)
				with self.assertRaises(frappe.PermissionError):
					via_rest(
						rest.create_doc,
						"Ruimtelid",
						data={
							"parenttype": "Ruimte",
							"parent": self.ruimte,
							"parentfield": "leden",
							"gebruiker": lid,
							"rol": "beheerder",
						},
					)
				with self.assertRaises(frappe.PermissionError):
					via_rest(rest.update_doc, "Ruimtelid", self.rij(lid), data={"rol": "beheerder"})
				with self.assertRaises(frappe.PermissionError):
					via_rest(rest.update_doc, "Ruimte", self.ruimte, data={"leden": [{"gebruiker": lid, "rol": "beheerder"}]})
				self.leden_ongewijzigd()

	def test_beheerder_van_de_ruimte_mag_leden_beheren(self):
		nieuwkomer = gebruiker("nieuwkomer")
		laatkomer = gebruiker("laatkomer")
		frappe.set_user(self.beheerder)
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.append("leden", {"gebruiker": self.buitenstaander, "rol": "lezer"})
		doc.save()
		self.assertEqual(rol_in_ruimte(self.ruimte, self.buitenstaander), "lezer")

		client_set_value("Ruimtelid", self.rij(self.buitenstaander), "rol", "schrijver")
		self.assertEqual(rol_in_ruimte(self.ruimte, self.buitenstaander), "schrijver")

		via_rest(rest.update_doc, "Ruimtelid", self.rij(self.lezer), data={"rol": "schrijver"})
		self.assertEqual(rol_in_ruimte(self.ruimte, self.lezer), "schrijver")

		client_insert(
			{
				"doctype": "Ruimtelid",
				"parenttype": "Ruimte",
				"parent": self.ruimte,
				"parentfield": "leden",
				"gebruiker": nieuwkomer,
				"rol": "gast",
			}
		)
		self.assertEqual(rol_in_ruimte(self.ruimte, nieuwkomer), "gast")

		# Dezelfde losse REST-aanvraag voor een Ruimtelid die een lid geweigerd wordt, lukt de beheerder wel.
		via_rest(
			rest.create_doc,
			"Ruimtelid",
			data={"parenttype": "Ruimte", "parent": self.ruimte, "parentfield": "leden", "gebruiker": laatkomer, "rol": "lezer"},
		)
		self.assertEqual(rol_in_ruimte(self.ruimte, laatkomer), "lezer")
