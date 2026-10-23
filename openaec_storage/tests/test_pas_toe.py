import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage import api, mapsjablonen, ruimtebeheer
from openaec_storage.tests.hulp import NagebootsteS3, foutcode, gebruiker, met_rol, ruim_op, ruimte, upload

AANTAL_BIM = len(mapsjablonen.STANDAARD["Project (BIM)"]["mappen"])


def laatste(ruimte_naam: str, soort: str) -> dict:
	return frappe.get_all(
		"Opslagwijziging",
		filters={"ruimte": ruimte_naam, "soort": soort},
		fields=["owner", "namens", "naam", "doel_doctype", "doel"],
		order_by="name desc",
		limit=1,
		ignore_permissions=True,
	)[0]


class TestPasToe(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		mapsjablonen.zorg_voor_standaardsjablonen()
		self.s3 = NagebootsteS3().start()
		self.beheerder = gebruiker("beheerder")
		self.schrijver = gebruiker("schrijver")
		self.ruimte = ruimte("Bestaand project", {self.beheerder: "beheerder", self.schrijver: "schrijver"})
		frappe.set_user(self.schrijver)
		# Een map die al bestaat (andere hoofdletters) met een bestand dat niet aan de nieuwe regel voldoet.
		self.tekeningen = api.maak_map(ruimte=self.ruimte, naam="06 tekeningen")["map"]
		upload(self.ruimte, "notitie.docx", b"doc", map_naam=self.tekeningen)
		upload(self.ruimte, "plan.pdf", b"pdf", map_naam=self.tekeningen)
		self.eigen = api.maak_map(ruimte=self.ruimte, naam="Eigen")["map"]
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.append("mapregels", {"map": self.eigen, "pad": "Eigen", "alleen_lezen": 1})
		doc.save()
		frappe.set_user(self.beheerder)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.s3.stop()
		ruim_op()

	def regels(self) -> dict:
		return {r["pad"]: r for r in mapsjablonen.regels(ruimte=self.ruimte)}

	def test_mappen_hergebruikt_en_aangevuld_bestanden_blijven(self):
		uitslag = mapsjablonen.pas_toe(ruimte=self.ruimte, mapsjabloon="Project (BIM)")
		self.assertEqual(
			uitslag,
			{
				"ruimte": self.ruimte,
				"mapsjabloon": "Project (BIM)",
				"mappen_aangemaakt": AANTAL_BIM - 1,
				"mappen_bestaand": 1,
				"regels": AANTAL_BIM,
				"afwijkende_bestanden": 1,
			},
		)
		self.assertEqual(frappe.db.get_value("Ruimte", self.ruimte, "mapsjabloon"), "Project (BIM)")
		# De bestaande map is gebruikt (naam ongewijzigd) en het afwijkende bestand staat er nog.
		self.assertEqual(self.regels()["06 Tekeningen"]["map"], self.tekeningen)
		self.assertEqual(frappe.db.get_value("Opslagmap", self.tekeningen, "naam"), "06 tekeningen")
		namen = [b["naam"] for b in api.bladeren(ruimte=self.ruimte, map=self.tekeningen)["bestanden"]]
		self.assertEqual(namen, ["notitie.docx", "plan.pdf"])
		# De regel op een andere map blijft staan.
		self.assertTrue(self.regels()["Eigen"]["alleen_lezen"])
		log = laatste(self.ruimte, "sjabloon_toegepast")
		self.assertEqual((log.owner, log.namens, log.naam), (self.beheerder, None, "Project (BIM)"))

	def test_twee_keer_toepassen_geeft_hetzelfde(self):
		mapsjablonen.pas_toe(ruimte=self.ruimte, mapsjabloon="Project (BIM)")
		mappen = frappe.db.count("Opslagmap", {"ruimte": self.ruimte})
		regels = self.regels()
		tweede = mapsjablonen.pas_toe(ruimte=self.ruimte, mapsjabloon="Project (BIM)")
		self.assertEqual((tweede["mappen_aangemaakt"], tweede["mappen_bestaand"]), (0, AANTAL_BIM))
		self.assertEqual(frappe.db.count("Opslagmap", {"ruimte": self.ruimte}), mappen)
		self.assertEqual(self.regels(), regels)

	def test_regels_worden_bijgewerkt_naar_het_sjabloon(self):
		mapsjablonen.pas_toe(ruimte=self.ruimte, mapsjabloon="Project (BIM)")
		frappe.set_user("Administrator")
		sjabloon = frappe.get_doc("Mapsjabloon", "Project (BIM)")
		rij = next(r for r in sjabloon.mappen if r.pad == "06 Tekeningen")
		oud = rij.extensies
		rij.extensies = "pdf, dwg, dxf, docx"
		sjabloon.save()
		try:
			frappe.set_user(self.beheerder)
			uitslag = mapsjablonen.pas_toe(ruimte=self.ruimte, mapsjabloon="Project (BIM)")
			self.assertEqual(self.regels()["06 Tekeningen"]["extensies"], ["docx", "dwg", "dxf", "pdf"])
			self.assertEqual(uitslag["afwijkende_bestanden"], 0)
		finally:
			frappe.set_user("Administrator")
			rij.extensies = oud
			sjabloon.save()

	def test_nieuwe_regels_gelden_meteen(self):
		mapsjablonen.pas_toe(ruimte=self.ruimte, mapsjabloon="Project (BIM)")
		frappe.set_user(self.schrijver)
		self.assertEqual(
			foutcode(
				self,
				frappe.ValidationError,
				api.upload_start,
				ruimte=self.ruimte,
				naam="nog een.docx",
				grootte=1,
				map=self.tekeningen,
			),
			"extensie_niet_toegestaan",
		)

	def test_alleen_voor_het_beheer(self):
		frappe.set_user(self.schrijver)
		with self.assertRaises(frappe.PermissionError):
			mapsjablonen.pas_toe(ruimte=self.ruimte, mapsjabloon="Project (BIM)")
		frappe.set_user(met_rol(gebruiker("omgeving"), "OpenAEC Beheerder"))
		self.assertEqual(mapsjablonen.pas_toe(ruimte=self.ruimte, mapsjabloon="Bedrijf")["mappen_aangemaakt"], 4)

	def test_onbekend_sjabloon(self):
		with self.assertRaises(frappe.DoesNotExistError):
			mapsjablonen.pas_toe(ruimte=self.ruimte, mapsjabloon="Bestaat niet")

	def test_gearchiveerde_ruimte_geweigerd(self):
		ruimtebeheer.archiveer(ruimte=self.ruimte)
		self.assertEqual(
			foutcode(
				self, frappe.PermissionError, mapsjablonen.pas_toe, ruimte=self.ruimte, mapsjabloon="Project (BIM)"
			),
			"ruimte_gearchiveerd",
		)
		self.assertFalse(frappe.db.get_value("Ruimte", self.ruimte, "mapsjabloon"))


class TestNamens(IntegrationTestCase):
	"""`namens` is alleen voor het auditspoor van een serviceaanroep uit het beheer; de rechten blijven die van de
	echte aanroeper."""

	def setUp(self):
		ruim_op()
		mapsjablonen.zorg_voor_standaardsjablonen()
		self.beheerder = gebruiker("beheerder")
		self.schrijver = gebruiker("schrijver")
		self.dienst = met_rol(gebruiker("dienst"), "System Manager")
		self.ruimte = ruimte("Namens", {self.beheerder: "beheerder", self.schrijver: "schrijver"})

	def tearDown(self):
		ruim_op()

	def test_serviceaanroep_legt_beide_vast(self):
		frappe.set_user(self.dienst)
		uitslag = ruimtebeheer.archiveer(ruimte=self.ruimte, namens=self.beheerder)
		self.assertEqual(uitslag["gearchiveerd_door"], self.beheerder)
		log = laatste(self.ruimte, "gearchiveerd")
		self.assertEqual((log.owner, log.namens), (self.dienst, self.beheerder))
		regel = ruimtebeheer.activiteit(ruimte=self.ruimte)["regels"][0]
		self.assertEqual(
			(regel["door"], regel["door_naam"], regel["namens"], regel["namens_naam"]),
			(self.dienst, "dienst", self.beheerder, "beheerder"),
		)
		ruimtebeheer.herstel(ruimte=self.ruimte, namens=self.beheerder)
		self.assertEqual(laatste(self.ruimte, "teruggezet").namens, self.beheerder)

	def test_administrator_mag_ook(self):
		frappe.set_user("Administrator")
		ruimtebeheer.zet_quotum(ruimte=self.ruimte, quotum_gb=5, namens=self.beheerder)
		log = laatste(self.ruimte, "quotum_gewijzigd")
		self.assertEqual((log.owner, log.namens, log.naam), ("Administrator", self.beheerder, "5 GB"))
		mapsjablonen.pas_toe(ruimte=self.ruimte, mapsjabloon="Bedrijf", namens=self.beheerder)
		self.assertEqual(laatste(self.ruimte, "sjabloon_toegepast").namens, self.beheerder)

	def test_van_een_ander_genegeerd_zonder_weigering(self):
		frappe.set_user(self.beheerder)
		uitslag = ruimtebeheer.archiveer(ruimte=self.ruimte, namens=self.schrijver)
		self.assertEqual(uitslag["gearchiveerd_door"], self.beheerder)
		log = laatste(self.ruimte, "gearchiveerd")
		self.assertEqual((log.owner, log.namens), (self.beheerder, None))
		self.assertIsNone(ruimtebeheer.activiteit(ruimte=self.ruimte)["regels"][0]["namens_naam"])

	def test_onbekende_of_uitgeschakelde_gebruiker_genegeerd(self):
		uit = gebruiker("uit")
		frappe.db.set_value("User", uit, "enabled", 0)
		frappe.set_user(self.dienst)
		ruimtebeheer.archiveer(ruimte=self.ruimte, namens="bestaat-niet@example.com")
		self.assertIsNone(laatste(self.ruimte, "gearchiveerd").namens)
		ruimtebeheer.herstel(ruimte=self.ruimte, namens=uit)
		self.assertIsNone(laatste(self.ruimte, "teruggezet").namens)

	def test_rechten_blijven_die_van_de_aanroeper(self):
		frappe.set_user(self.schrijver)
		with self.assertRaises(frappe.PermissionError):
			ruimtebeheer.archiveer(ruimte=self.ruimte, namens=self.beheerder)
		with self.assertRaises(frappe.PermissionError):
			ruimtebeheer.zet_quotum(ruimte=self.ruimte, quotum_gb=5, namens=self.dienst)
