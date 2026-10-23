import frappe
import requests
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from openaec_storage import api, delen, mapsjablonen, ruimtebeheer
from openaec_storage.tests.hulp import NagebootsteS3, foutcode, gebruiker, met_rol, ruim_op, upload

ALLEEN_LEZEN = "map_alleen_lezen"
EXTENSIE = "extensie_niet_toegestaan"


def ruimte_met_sjabloon(naam: str, sjabloon: str, leden: dict[str, str]) -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "Ruimte",
				"naam": naam,
				"soort": "project",
				"mapsjabloon": sjabloon,
				"leden": [{"gebruiker": g, "rol": r} for g, r in leden.items()],
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def map_op_pad(ruimte_naam: str, pad: str) -> str:
	bovenliggende = None
	for deel in pad.split("/"):
		bovenliggende = frappe.db.get_value(
			"Opslagmap",
			{"ruimte": ruimte_naam, "naam": deel, "bovenliggende": bovenliggende or ("is", "not set")},
			"name",
		)
	return bovenliggende


class TestStandaardsjablonen(IntegrationTestCase):
	def test_seed_is_idempotent_en_laat_aanpassingen_staan(self):
		mapsjablonen.zorg_voor_standaardsjablonen()
		doc = frappe.get_doc("Mapsjabloon", "Bedrijf")
		doc.omschrijving = "Aangepast door de klant"
		doc.save(ignore_permissions=True)
		mapsjablonen.zorg_voor_standaardsjablonen()
		self.assertEqual(frappe.db.count("Mapsjabloon", {"name": ("in", ["Bedrijf", "Project (BIM)"])}), 2)
		self.assertEqual(frappe.db.get_value("Mapsjabloon", "Bedrijf", "omschrijving"), "Aangepast door de klant")

	def test_bedrijf_heeft_de_afgesproken_mappen(self):
		mapsjablonen.zorg_voor_standaardsjablonen()
		doc = frappe.get_doc("Mapsjabloon", "Bedrijf")
		self.assertEqual(doc.soort, "bedrijf")
		self.assertEqual([m.pad for m in doc.mappen], ["Algemeen", "Contracten", "Projecten", "Huisstijl"])
		self.assertEqual(frappe.db.get_value("Mapsjabloon", "Project (BIM)", "soort"), "project")


class TestMapsjabloon(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Mapsjabloon", {"name": ("like", "Test %")})

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Mapsjabloon", {"name": ("like", "Test %")})
		ruim_op()

	def sjabloon(self, naam, mappen, soort="team", standaard=0):
		return frappe.get_doc(
			{"doctype": "Mapsjabloon", "naam": naam, "soort": soort, "standaard": standaard, "mappen": mappen}
		).insert(ignore_permissions=True)

	def test_paden_en_extensies_worden_netjes(self):
		doc = self.sjabloon("Test netjes", [{"pad": " Projecten\\\\Lopend/ ", "extensies": ".PDF; dwg ,pdf"}])
		self.assertEqual(doc.mappen[0].pad, "Projecten/Lopend")
		self.assertEqual(doc.mappen[0].weergavenaam, "Lopend")
		self.assertEqual(doc.mappen[0].extensies, "dwg, pdf")

	def test_dubbel_pad_geweigerd(self):
		with self.assertRaises(frappe.ValidationError):
			self.sjabloon("Test dubbel", [{"pad": "Tekeningen"}, {"pad": "tekeningen"}])

	def test_een_standaard_per_soort(self):
		self.sjabloon("Test een", [{"pad": "A"}], standaard=1)
		self.sjabloon("Test twee", [{"pad": "B"}], standaard=1)
		self.assertEqual(frappe.db.get_value("Mapsjabloon", "Test een", "standaard"), 0)
		self.assertEqual(frappe.db.get_value("Mapsjabloon", "Test twee", "standaard"), 1)

	def test_kiezen_mag_iedereen_beheren_alleen_de_omgeving(self):
		self.sjabloon("Test rechten", [{"pad": "A"}])
		frappe.set_user(gebruiker("schrijver"))
		self.assertTrue(frappe.has_permission("Mapsjabloon", "read", "Test rechten"))
		self.assertFalse(frappe.has_permission("Mapsjabloon", "write", "Test rechten"))
		self.assertIn("Test rechten", [s["naam"] for s in mapsjablonen.sjablonen(soort="team")])
		frappe.set_user(met_rol(gebruiker("omgeving"), "OpenAEC Beheerder"))
		self.assertTrue(frappe.has_permission("Mapsjabloon", "write", "Test rechten"))

	def test_bij_team_ook_de_bedrijfssjablonen(self):
		self.sjabloon("Test team", [{"pad": "A"}], soort="team")
		self.sjabloon("Test bedrijf", [{"pad": "B"}], soort="bedrijf")
		self.sjabloon("Test project", [{"pad": "C"}], soort="project")

		def namen(soort):
			return {s["naam"] for s in mapsjablonen.sjablonen(soort=soort) if s["naam"].startswith("Test ")}
		self.assertEqual(namen("team"), {"Test team", "Test bedrijf"})
		self.assertEqual(namen("bedrijf"), {"Test bedrijf"})
		self.assertEqual(namen("project"), {"Test project"})
		self.assertEqual(namen(None), {"Test team", "Test bedrijf", "Test project"})

	def test_mappen_worden_aangelegd_ook_tussenliggende(self):
		self.sjabloon("Test diep", [{"pad": "Ontwerp/Constructie/Berekeningen", "volgorde": 2}, {"pad": "Algemeen", "volgorde": 1}])
		naam = ruimte_met_sjabloon("Diep", "Test diep", {})
		self.assertTrue(map_op_pad(naam, "Ontwerp/Constructie/Berekeningen"))
		self.assertEqual(
			[r["pad"] for r in mapsjablonen.regels(ruimte=naam)], ["Algemeen", "Ontwerp/Constructie/Berekeningen"]
		)

	def test_ruimte_verandert_niet_mee_met_het_sjabloon(self):
		self.sjabloon("Test vast", [{"pad": "Tekeningen", "extensies": "pdf"}])
		naam = ruimte_met_sjabloon("Vast", "Test vast", {})
		doc = frappe.get_doc("Mapsjabloon", "Test vast")
		doc.mappen[0].extensies = "dwg"
		doc.append("mappen", {"pad": "Nieuw"})
		doc.save(ignore_permissions=True)
		self.assertEqual([(r["pad"], r["extensies"]) for r in mapsjablonen.regels(ruimte=naam)], [("Tekeningen", ["pdf"])])
		self.assertFalse(map_op_pad(naam, "Nieuw"))

	def test_regel_met_map_uit_andere_ruimte_geweigerd(self):
		self.sjabloon("Test ander", [{"pad": "A"}])
		een = ruimte_met_sjabloon("Een", "Test ander", {})
		twee = ruimte_met_sjabloon("Twee", "Test ander", {})
		doc = frappe.get_doc("Ruimte", twee)
		doc.mapregels[0].map = map_op_pad(een, "A")
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)


class TestMapregels(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagdeling")
		mapsjablonen.zorg_voor_standaardsjablonen()
		self.s3 = NagebootsteS3().start()
		self.beheerder = gebruiker("beheerder")
		self.schrijver = gebruiker("schrijver")
		self.lezer = gebruiker("lezer")
		self.bedrijf = ruimte_met_sjabloon(
			"Bedrijf BV", "Bedrijf", {self.beheerder: "beheerder", self.schrijver: "schrijver", self.lezer: "lezer"}
		)
		self.project = ruimte_met_sjabloon(
			"Project BIM", "Project (BIM)", {self.beheerder: "beheerder", self.schrijver: "schrijver"}
		)
		self.huisstijl = map_op_pad(self.bedrijf, "Huisstijl")
		self.algemeen = map_op_pad(self.bedrijf, "Algemeen")
		self.tekeningen = map_op_pad(self.project, "06 Tekeningen")
		frappe.set_user(self.beheerder)
		self.logo = upload(self.bedrijf, "logo.png", b"png", map_naam=self.huisstijl)["bestand"]
		frappe.set_user(self.schrijver)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.s3.stop()
		frappe.db.delete("Opslagdeling")
		ruim_op()

	def test_bedrijf_krijgt_de_vier_mappen_en_de_sync_ziet_ze(self):
		mappen = [m["naam"] for m in api.bladeren(ruimte=self.bedrijf)["mappen"]]
		self.assertEqual(sorted(mappen), ["Algemeen", "Contracten", "Huisstijl", "Projecten"])
		nieuw = [w for w in api.wijzigingen(ruimte=self.bedrijf)["wijzigingen"] if w["doel_doctype"] == "Opslagmap"]
		self.assertEqual(len(nieuw), 4)
		regels = {r["pad"]: r for r in mapsjablonen.regels(ruimte=self.bedrijf)}
		self.assertTrue(regels["Huisstijl"]["alleen_lezen"])
		self.assertEqual(regels["Huisstijl"]["map"], self.huisstijl)

	def test_project_bim_heeft_een_bouwindeling(self):
		self.assertTrue(map_op_pad(self.project, "05 BIM-modellen/Gepubliceerd"))
		self.assertTrue(map_op_pad(self.project, "04 Ontwerp/Constructie"))

	def test_alleen_lezen_voor_iedereen_behalve_de_beheerder(self):
		pogingen = [
			(api.upload_start, {"ruimte": self.bedrijf, "naam": "nieuw.png", "grootte": 1, "map": self.huisstijl}),
			(api.maak_map, {"ruimte": self.bedrijf, "naam": "Oud", "bovenliggende": self.huisstijl}),
			(api.verwijder, {"bestand": self.logo}),
			(api.verplaats, {"bestand": self.logo, "naam": "logo.png", "map": self.algemeen}),
			(api.verplaats_map, {"map": self.huisstijl, "naam": "Stijl"}),
			(api.verwijder_map, {"map": self.huisstijl}),
			(api.vergrendel, {"bestand": self.logo}),
		]
		for functie, argumenten in pogingen:
			with self.subTest(functie=functie.__name__):
				self.assertEqual(foutcode(self, frappe.PermissionError, functie, **argumenten), ALLEEN_LEZEN)
		# Lezen en downloaden gaan gewoon.
		self.assertEqual(requests.get(api.download(bestand=self.logo)["url"], timeout=30).content, b"png")
		frappe.set_user(self.beheerder)
		self.assertEqual(upload(self.bedrijf, "logo-wit.png", b"wit", map_naam=self.huisstijl)["versie"], 1)
		sub = api.maak_map(ruimte=self.bedrijf, naam="Lettertypes", bovenliggende=self.huisstijl)["map"]
		frappe.set_user(self.schrijver)
		self.assertEqual(
			foutcode(self, frappe.PermissionError, api.upload_start, ruimte=self.bedrijf, naam="a.otf", grootte=1, map=sub),
			ALLEEN_LEZEN,
		)

	def test_bestaande_map_opvragen_in_alleen_lezen_map_mag(self):
		frappe.set_user(self.beheerder)
		sub = api.maak_map(ruimte=self.bedrijf, naam="Logo's", bovenliggende=self.huisstijl)["map"]
		frappe.set_user(self.schrijver)
		self.assertEqual(api.maak_map(ruimte=self.bedrijf, naam="Logo's", bovenliggende=self.huisstijl)["map"], sub)

	def test_map_met_een_alleen_lezen_submap_niet_weg_te_gooien(self):
		bim = map_op_pad(self.project, "05 BIM-modellen")
		self.assertEqual(foutcode(self, frappe.PermissionError, api.verwijder_map, map=bim), ALLEEN_LEZEN)
		frappe.set_user(self.beheerder)
		api.verwijder_map(map=bim)
		self.assertTrue(frappe.db.get_value("Opslagmap", bim, "verwijderd_op"))

	def test_formulier_volgt_de_regel(self):
		self.assertTrue(frappe.has_permission("Opslagbestand", "read", self.logo))
		self.assertFalse(frappe.has_permission("Opslagbestand", "write", self.logo))
		frappe.set_user(self.beheerder)
		self.assertTrue(frappe.has_permission("Opslagbestand", "write", self.logo))

	def test_extensies_gelden_voor_iedereen(self):
		for wie in (self.schrijver, self.beheerder):
			frappe.set_user(wie)
			with self.subTest(wie=wie):
				self.assertEqual(
					foutcode(
						self,
						frappe.ValidationError,
						api.upload_start,
						ruimte=self.project,
						naam="notitie.docx",
						grootte=1,
						map=self.tekeningen,
					),
					EXTENSIE,
				)
		frappe.set_user(self.schrijver)
		plan = upload(self.project, "Plattegrond.PDF", b"pdf", map_naam=self.tekeningen)
		self.assertEqual(plan["versie"], 1)
		self.assertEqual(
			foutcode(self, frappe.ValidationError, api.verplaats, bestand=plan["bestand"], naam="plattegrond.docx", map=self.tekeningen),
			EXTENSIE,
		)
		los = upload(self.project, "brief.docx", b"doc")["bestand"]
		self.assertEqual(
			foutcode(self, frappe.ValidationError, api.verplaats, bestand=los, naam="brief.docx", map=self.tekeningen),
			EXTENSIE,
		)

	def test_melding_noemt_de_map_en_de_toegestane_soorten(self):
		with self.assertRaises(frappe.ValidationError) as fout:
			api.upload_start(ruimte=self.project, naam="x.txt", grootte=1, map=self.tekeningen)
		self.assertIn("06 Tekeningen", str(fout.exception))
		self.assertIn(".dwg", str(fout.exception))

	def test_sync_en_webdav_volgen_de_regels(self):
		# De desktop-app en de gateway gebruiken dezelfde API; ook een upload die al liep, wordt aan het eind getoetst.
		start = api.upload_start(ruimte=self.project, naam="plan.pdf", grootte=3, map=self.tekeningen, bron="webdav")
		antwoord = requests.put(start["delen"][0]["url"], data=b"abc", timeout=30)
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Ruimte", self.project)
		next(r for r in doc.mapregels if r.map == self.tekeningen).extensies = "dwg"
		doc.save()
		frappe.set_user(self.schrijver)
		self.assertEqual(
			foutcode(
				self,
				frappe.ValidationError,
				api.upload_klaar,
				upload=start["upload"],
				delen=[{"nummer": 1, "etag": antwoord.headers["ETag"]}],
			),
			EXTENSIE,
		)

	def test_uploadlink_volgt_de_regels(self):
		frappe.set_user(self.beheerder)
		link_huisstijl = api.deel(doel_doctype="Opslagmap", doel=self.huisstijl, rechten="uploaden")
		link_tekeningen = api.deel(doel_doctype="Opslagmap", doel=self.tekeningen, rechten="uploaden")
		frappe.set_user("Guest")
		self.assertEqual(
			foutcode(self, frappe.PermissionError, delen.upload_start, token=link_huisstijl["token"], naam="a.png", grootte=1),
			ALLEEN_LEZEN,
		)
		self.assertEqual(
			foutcode(self, frappe.ValidationError, delen.upload_start, token=link_tekeningen["token"], naam="a.zip", grootte=1),
			EXTENSIE,
		)


class TestActiviteit(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		self.s3 = NagebootsteS3().start()
		self.beheerder = gebruiker("beheerder")
		self.schrijver = gebruiker("schrijver")
		self.ruimte = frappe.get_doc(
			{
				"doctype": "Ruimte",
				"naam": "Log",
				"soort": "team",
				"leden": [{"gebruiker": self.beheerder, "rol": "beheerder"}, {"gebruiker": self.schrijver, "rol": "schrijver"}],
			}
		).insert(ignore_permissions=True).name
		frappe.set_user(self.schrijver)
		for i in range(5):
			upload(self.ruimte, f"bestand {i}.txt", b"x")

	def tearDown(self):
		frappe.set_user("Administrator")
		self.s3.stop()
		ruim_op()

	def test_beheerder_leest_het_log_nieuwste_eerst_in_paginas(self):
		frappe.set_user(self.beheerder)
		eerste = ruimtebeheer.activiteit(ruimte=self.ruimte, aantal=3)
		self.assertEqual((eerste["totaal"], eerste["aantal"], eerste["meer"]), (5, 3, True))
		self.assertEqual(eerste["regels"][0]["naam"], "bestand 4.txt")
		self.assertEqual(eerste["regels"][0]["door"], self.schrijver)
		self.assertEqual(eerste["regels"][0]["door_naam"], "schrijver")
		tweede = ruimtebeheer.activiteit(ruimte=self.ruimte, start=3, aantal=3)
		self.assertEqual([r["naam"] for r in tweede["regels"]], ["bestand 1.txt", "bestand 0.txt"])
		self.assertFalse(tweede["meer"])

	def test_vanaf_filtert_op_tijd(self):
		frappe.set_user(self.beheerder)
		self.assertEqual(ruimtebeheer.activiteit(ruimte=self.ruimte, vanaf=str(add_to_date(now_datetime(), hours=1)))["totaal"], 0)
		self.assertEqual(ruimtebeheer.activiteit(ruimte=self.ruimte, vanaf="2000-01-01")["totaal"], 5)

	def test_alleen_voor_het_beheer(self):
		with self.assertRaises(frappe.PermissionError):
			ruimtebeheer.activiteit(ruimte=self.ruimte)
		frappe.set_user(met_rol(gebruiker("omgeving"), "OpenAEC Beheerder"))
		self.assertEqual(ruimtebeheer.activiteit(ruimte=self.ruimte)["totaal"], 5)
