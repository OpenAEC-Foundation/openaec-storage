from unittest.mock import patch

import frappe
import requests
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from openaec_storage import api, ruimtebeheer
from openaec_storage.tests.hulp import NagebootsteS3, foutcode, gebruiker, ruim_op, ruimte, upload


def item(naam: str, inhoud: bytes, **extra) -> dict:
	return {"naam": naam, "grootte": len(inhoud), "blokhash": "h-" + naam, **extra}


class GedeeldBasis(IntegrationTestCase):
	"""Ruimte met een map Tekeningen (met Details en een bestand) en een map Financieel ernaast. Bea is geen lid."""

	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagdeling")
		frappe.db.delete("Notification Log", {"for_user": ("like", "%@storage-test.localhost")})
		self.s3 = NagebootsteS3().start()
		self.anna = gebruiker("anna")
		self.bea = gebruiker("bea")
		self.lid = gebruiker("lid")
		self.ruimte = ruimte("Project Haven", {self.anna: "beheerder", self.lid: "lezer"})
		frappe.set_user(self.anna)
		self.tekeningen = api.maak_map(ruimte=self.ruimte, naam="Tekeningen")["map"]
		self.details = api.maak_map(ruimte=self.ruimte, naam="Details", bovenliggende=self.tekeningen)["map"]
		self.financieel = api.maak_map(ruimte=self.ruimte, naam="Financieel")["map"]
		self.plan = upload(self.ruimte, "plan.pdf", b"plan", map_naam=self.tekeningen)["bestand"]
		self.detail = upload(self.ruimte, "detail.dwg", b"detail", map_naam=self.details)["bestand"]
		self.begroting = upload(self.ruimte, "begroting.xlsx", b"geld", map_naam=self.financieel)["bestand"]
		self.los = upload(self.ruimte, "los.txt", b"los")["bestand"]

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Opslagdeling")
		self.s3.stop()
		ruim_op()

	def deel(self, doctype: str, doel: str, rechten: str = "schrijven", wie: str | None = None, **extra) -> str:
		frappe.set_user(self.anna)
		with patch("frappe.sendmail"):
			deling = api.deel(doctype, doel, soort="gebruiker", gebruiker=wie or self.bea, rechten=rechten, **extra)["deling"]
		frappe.set_user(self.bea)
		return deling

	def inhoud(self, bestand: str) -> bytes:
		return requests.get(api.download(bestand=bestand)["url"], timeout=30).content


class TestGedeeldMetMij(GedeeldBasis):
	def test_lijst_met_wie_deelde_rechten_en_vervaldatum(self):
		verloopt = str(add_to_date(now_datetime(), days=7))
		map_deling = self.deel("Opslagmap", self.tekeningen, "schrijven", verloopt=verloopt)
		bestand_deling = self.deel("Opslagbestand", self.begroting, "lezen")
		lijst = api.gedeeld_met_mij()
		self.assertEqual([r["deling"] for r in lijst], [bestand_deling, map_deling])
		bestand, map = lijst
		self.assertEqual(
			{k: map[k] for k in ("doel_doctype", "doel", "naam", "ruimte", "ruimte_naam", "rechten", "gedeeld_door", "al_lid")},
			{
				"doel_doctype": "Opslagmap",
				"doel": self.tekeningen,
				"naam": "Tekeningen",
				"ruimte": self.ruimte,
				"ruimte_naam": "Project Haven",
				"rechten": "schrijven",
				"gedeeld_door": self.anna,
				"al_lid": False,
			},
		)
		self.assertEqual(map["gedeeld_door_naam"], "anna")
		self.assertTrue(map["sinds"])
		self.assertEqual(str(map["verloopt"])[:16], str(verloopt)[:16])
		self.assertEqual((bestand["naam"], bestand["rechten"], bestand["map"], bestand["versie"]), ("begroting.xlsx", "lezen", self.financieel, 1))

	def test_alleen_wat_nu_geldt(self):
		ingetrokken = self.deel("Opslagmap", self.tekeningen)
		verlopen = self.deel("Opslagmap", self.details, verloopt=str(add_to_date(now_datetime(), hours=-1)))
		weg = self.deel("Opslagbestand", self.los)
		frappe.set_user(self.anna)
		api.trek_in(ingetrokken)
		api.verwijder(self.los)
		with patch("frappe.sendmail"):
			api.deel("Opslagmap", self.financieel, soort="link")  # een link is niet "met mij gedeeld"
			api.deel("Opslagmap", self.financieel, soort="gebruiker", gebruiker=self.lid)  # met een ander
		frappe.set_user(self.bea)
		self.assertEqual(api.gedeeld_met_mij(), [])
		self.assertTrue(verlopen and weg)

	def test_gearchiveerde_ruimte_valt_weg(self):
		self.deel("Opslagmap", self.tekeningen)
		self.assertEqual(len(api.gedeeld_met_mij()), 1)
		frappe.set_user(self.anna)
		ruimtebeheer.archiveer(self.ruimte)
		frappe.set_user(self.bea)
		self.assertEqual(api.gedeeld_met_mij(), [])

	def test_lid_van_de_ruimte_krijgt_al_lid(self):
		self.deel("Opslagmap", self.details, "schrijven", wie=self.lid)
		frappe.set_user(self.lid)
		(regel,) = api.gedeeld_met_mij()
		self.assertEqual((regel["al_lid"], regel["rol"], regel["rechten"]), (True, "lezer", "schrijven"))

	def test_twee_keer_gedeeld_geeft_een_regel_met_het_hoogste_recht(self):
		self.deel("Opslagmap", self.tekeningen, "lezen")
		schrijven = self.deel("Opslagmap", self.tekeningen, "schrijven")
		self.deel("Opslagmap", self.tekeningen, "lezen")
		(regel,) = api.gedeeld_met_mij()
		self.assertEqual((regel["deling"], regel["rechten"]), (schrijven, "schrijven"))


class TestWortel(GedeeldBasis):
	"""Een gedeelde map als bovenste map: binnen de wortel alles wat de rechten toestaan, erbuiten niets."""

	def setUp(self):
		super().setUp()
		self.deling = self.deel("Opslagmap", self.tekeningen, "schrijven")

	def test_bladeren_begint_bij_de_gedeelde_map(self):
		inhoud = api.bladeren(deling=self.deling)
		self.assertEqual([m.naam for m in inhoud["mappen"]], ["Details"])
		self.assertEqual([b.naam for b in inhoud["bestanden"]], ["plan.pdf"])
		self.assertEqual([b.naam for b in api.bladeren(deling=self.deling, map=self.details)["bestanden"]], ["detail.dwg"])
		# Zonder deling, met de gedeelde map: dat kon al.
		self.assertEqual(len(api.bladeren(ruimte=self.ruimte, map=self.tekeningen)["bestanden"]), 1)

	def test_buiten_de_wortel_geweigerd(self):
		self.assertEqual(foutcode(self, frappe.PermissionError, api.bladeren, deling=self.deling, map=self.financieel), "buiten_deling")
		self.assertEqual(foutcode(self, frappe.PermissionError, api.bladeren, ruimte=self.ruimte), "buiten_deling")
		self.assertEqual(foutcode(self, frappe.PermissionError, api.download, bestand=self.begroting), "buiten_deling")
		self.assertEqual(foutcode(self, frappe.PermissionError, api.download, bestand=self.los), "buiten_deling")
		self.assertEqual(
			foutcode(self, frappe.PermissionError, api.maak_map, deling=self.deling, naam="X", bovenliggende=self.financieel),
			"buiten_deling",
		)
		# Iemand anders' deling gebruiken kan niet.
		frappe.set_user(self.lid)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.bladeren, deling=self.deling), "deling_onbekend")

	def test_niet_omhoog_of_naar_een_andere_map(self):
		self.assertEqual(
			foutcode(self, frappe.PermissionError, api.verplaats, bestand=self.plan, naam="plan.pdf", map=self.financieel),
			"buiten_deling",
		)
		self.assertEqual(
			foutcode(self, frappe.PermissionError, api.verplaats, bestand=self.plan, naam="plan.pdf", map=None), "buiten_deling"
		)
		self.assertEqual(
			foutcode(self, frappe.PermissionError, api.verplaats_map, map=self.details, naam="Details", bovenliggende=None),
			"buiten_deling",
		)
		# De gedeelde map zelf hernoemen, verplaatsen of weghalen gaat niet: de map erboven is niet gedeeld.
		self.assertEqual(
			foutcode(self, frappe.PermissionError, api.verplaats_map, map=self.tekeningen, naam="Andere naam"), "buiten_deling"
		)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.verwijder_map, map=self.tekeningen), "buiten_deling")
		self.assertFalse(frappe.db.get_value("Opslagmap", self.tekeningen, "verwijderd_op"))
		# Binnen de wortel wel.
		api.verplaats(bestand=self.plan, naam="plan.pdf", map=self.details)
		api.verplaats_map(map=self.details, naam="Detailtekeningen", bovenliggende=self.tekeningen)
		api.verwijder_map(map=self.details)
		self.assertTrue(frappe.db.get_value("Opslagmap", self.details, "verwijderd_op"))

	def test_boom_alleen_onder_de_wortel(self):
		boom = api.boom(deling=self.deling)
		self.assertEqual(boom["wortel"], self.tekeningen)
		self.assertEqual([(m.name, m.bovenliggende) for m in boom["mappen"]], [(self.details, self.tekeningen)])
		self.assertEqual(sorted(b.naam for b in boom["bestanden"]), ["detail.dwg", "plan.pdf"])
		# Zonder deling blijft de boom voor leden.
		self.assertRaises(frappe.PermissionError, api.boom, ruimte=self.ruimte)

	def test_uploaden_en_mappen_binnen_de_wortel(self):
		r = upload(self.ruimte, "nieuw.txt", b"nieuw", map_naam=self.tekeningen)
		self.assertEqual(frappe.db.get_value("Opslagbestand", r["bestand"], "map"), self.tekeningen)
		self.assertEqual(api.maak_map(deling=self.deling, naam="Schetsen")["nieuw"], True)
		mappen = api.maak_mappen(deling=self.deling, paden=["Details/2026", "Nieuw/Diep"])
		self.assertEqual(mappen["fouten"], {})
		self.assertEqual(frappe.db.get_value("Opslagmap", mappen["mappen"]["Details/2026"], "bovenliggende"), self.details)
		nieuw = frappe.db.get_value("Opslagmap", mappen["mappen"]["Nieuw/Diep"], "bovenliggende")
		self.assertEqual(frappe.db.get_value("Opslagmap", nieuw, "bovenliggende"), self.tekeningen)
		# Een pad met '..' is gewoon een (ongeldige) naam, geen weg naar boven.
		fout = api.maak_mappen(deling=self.deling, paden=["../Financieel"])["fouten"]["../Financieel"]
		self.assertEqual(fout["foutcode"], "ongeldige_naam")

	def test_wijzigingen_alleen_binnen_de_wortel(self):
		frappe.set_user(self.anna)
		upload(self.ruimte, "begroting.xlsx", b"meer geld", map_naam=self.financieel, basis_versie=1)
		upload(self.ruimte, "detail.dwg", b"detail 2", map_naam=self.details, basis_versie=1)
		api.verwijder_map(map=self.details)
		frappe.set_user(self.bea)
		uit = api.wijzigingen(deling=self.deling)
		doelen = {(w["doel_doctype"], w["doel"]) for w in uit["wijzigingen"]}
		self.assertIn(("Opslagbestand", self.detail), doelen)
		self.assertIn(("Opslagmap", self.details), doelen)  # ook een verwijderde map
		self.assertIn(("Opslagmap", self.tekeningen), doelen)  # het delen zelf
		self.assertNotIn(("Opslagbestand", self.begroting), doelen)
		self.assertNotIn(("Opslagmap", self.financieel), doelen)
		self.assertGreaterEqual(uit["cursor"], max(w["volgnummer"] for w in uit["wijzigingen"]))
		self.assertRaises(frappe.PermissionError, api.wijzigingen, ruimte=self.ruimte)

	def test_bestandsdeling_is_geen_wortel(self):
		deling = self.deel("Opslagbestand", self.begroting, "schrijven")
		self.assertEqual(foutcode(self, frappe.ValidationError, api.boom, deling=deling), "deling_geen_map")


class TestLezenEnSchrijven(GedeeldBasis):
	def test_lezen_mag_bekijken_en_downloaden(self):
		deling = self.deel("Opslagmap", self.tekeningen, "lezen")
		self.assertEqual(self.inhoud(self.plan), b"plan")
		self.assertEqual(len(api.boom(deling=deling)["bestanden"]), 2)
		self.assertEqual(api.versies(bestand=self.detail)[0]["volgnummer"], 1)
		klein = api.download_klein(bestanden=[self.plan, self.begroting])["bestanden"]
		self.assertTrue(klein[0]["ok"])
		self.assertEqual((klein[1]["ok"], klein[1]["foutcode"]), (False, "buiten_deling"))

	def test_lezen_weigert_wijzigen_met_alleen_lezen_deling(self):
		deling = self.deel("Opslagmap", self.tekeningen, "lezen")
		code = "alleen_lezen_deling"
		self.assertEqual(foutcode(self, frappe.PermissionError, upload, self.ruimte, "x.txt", b"x", map_naam=self.tekeningen), code)
		self.assertEqual(
			foutcode(self, frappe.PermissionError, upload, self.ruimte, "plan.pdf", b"x", map_naam=self.tekeningen, basis_versie=1),
			code,
		)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.verwijder, bestand=self.plan), code)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.verplaats, bestand=self.plan, naam="p.pdf", map=self.tekeningen), code)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.verwijder_map, map=self.details), code)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.maak_map, deling=deling, naam="Nieuw"), code)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.vergrendel, bestand=self.plan), code)
		self.assertEqual(api.maak_mappen(deling=deling, paden=["Nieuw"])["fouten"]["Nieuw"]["foutcode"], code)
		(r,) = api.upload_klein_bytes(None, [item("y.txt", b"y")], {"f0": b"y"}, deling=deling)["resultaten"]
		self.assertEqual((r["ok"], r["foutcode"]), (False, code))
		self.assertEqual(frappe.db.get_value("Opslagbestand", self.plan, "naam"), "plan.pdf")

	def test_schrijven_mag_alles_binnen_de_map(self):
		deling = self.deel("Opslagmap", self.tekeningen, "schrijven")
		r = upload(self.ruimte, "plan.pdf", b"plan 2", map_naam=self.tekeningen, basis_versie=1)
		self.assertEqual((r["bestand"], r["versie"], r["conflict"]), (self.plan, 2, False))
		resultaten = api.upload_klein_bytes(
			None,
			[item("a.txt", b"a"), item("b.txt", b"b", pad="Details/Nieuw"), item("c.txt", b"c", map=self.financieel)],
			{"f0": b"a", "f1": b"b", "f2": b"c"},
			deling=deling,
		)["resultaten"]
		self.assertEqual([x["ok"] for x in resultaten], [True, True, False])
		self.assertEqual(resultaten[0]["map"], self.tekeningen)
		self.assertEqual(frappe.db.get_value("Opslagmap", resultaten[1]["map"], "bovenliggende"), self.details)
		self.assertEqual(resultaten[2]["foutcode"], "buiten_deling")
		api.verwijder(bestand=self.detail)
		self.assertTrue(frappe.db.get_value("Opslagbestand", self.detail, "verwijderd_op"))

	def test_gedeeld_bestand_bewerken_maar_niet_weghalen(self):
		self.deel("Opslagbestand", self.begroting, "schrijven")
		self.assertEqual(self.inhoud(self.begroting), b"geld")
		r = upload(self.ruimte, "begroting.xlsx", b"nieuw geld", map_naam=self.financieel, basis_versie=1)
		self.assertEqual((r["bestand"], r["versie"]), (self.begroting, 2))
		# Alleen dat ene bestand: niets ernaast, en het bestand zelf niet weghalen of hernoemen.
		self.assertEqual(
			foutcode(self, frappe.PermissionError, upload, self.ruimte, "ander.xlsx", b"x", map_naam=self.financieel), "buiten_deling"
		)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.verwijder, bestand=self.begroting), "buiten_deling")
		self.assertEqual(
			foutcode(self, frappe.PermissionError, api.verplaats, bestand=self.begroting, naam="b.xlsx", map=self.financieel),
			"buiten_deling",
		)

	def test_gedeeld_bestand_met_lezen(self):
		self.deel("Opslagbestand", self.begroting, "lezen")
		self.assertEqual(self.inhoud(self.begroting), b"geld")
		self.assertEqual(
			foutcode(self, frappe.PermissionError, upload, self.ruimte, "begroting.xlsx", b"x", map_naam=self.financieel, basis_versie=1),
			"alleen_lezen_deling",
		)


class TestIntrekkenEnVerlopen(GedeeldBasis):
	def test_intrekken_stopt_de_toegang_direct(self):
		deling = self.deel("Opslagmap", self.tekeningen, "schrijven")
		start = api.upload_start(ruimte=self.ruimte, naam="lopend.txt", grootte=5, map=self.tekeningen)
		antwoord = requests.put(start["delen"][0]["url"], data=b"lopen", timeout=30)
		frappe.set_user(self.anna)
		api.trek_in(deling)
		frappe.set_user(self.bea)
		code = "deling_ingetrokken"
		# Ook een upload die al liep (zoals midden in een sync) wordt niet meer afgerond.
		self.assertEqual(
			foutcode(self, frappe.PermissionError, api.upload_klaar, upload=start["upload"], delen=[{"nummer": 1, "etag": antwoord.headers["ETag"]}]),
			code,
		)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.bladeren, deling=deling), code)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.boom, deling=deling), code)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.wijzigingen, deling=deling), code)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.download, bestand=self.plan), code)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.bladeren, ruimte=self.ruimte, map=self.details), code)
		(klein,) = api.download_klein(bestanden=[self.detail])["bestanden"]
		self.assertEqual(klein["foutcode"], code)
		self.assertFalse(frappe.db.exists("Opslagbestand", {"naam": "lopend.txt"}))

	def test_verlopen_deling(self):
		deling = self.deel("Opslagmap", self.tekeningen, "lezen", verloopt=str(add_to_date(now_datetime(), hours=1)))
		self.assertEqual(self.inhoud(self.plan), b"plan")
		frappe.db.set_value("Opslagdeling", deling, "verloopt", add_to_date(now_datetime(), minutes=-1))
		self.assertEqual(foutcode(self, frappe.PermissionError, api.bladeren, deling=deling), "deling_verlopen")
		self.assertEqual(foutcode(self, frappe.PermissionError, api.download, bestand=self.plan), "deling_verlopen")

	def test_verwijderde_gedeelde_map(self):
		deling = self.deel("Opslagmap", self.details, "schrijven")
		frappe.set_user(self.anna)
		api.verwijder_map(map=self.details)
		frappe.set_user(self.bea)
		self.assertEqual(foutcode(self, frappe.DoesNotExistError, api.boom, deling=deling), "deling_weg")

	def test_een_andere_deling_blijft_werken(self):
		ene = self.deel("Opslagmap", self.tekeningen, "lezen")
		self.deel("Opslagmap", self.details, "lezen")
		frappe.set_user(self.anna)
		api.trek_in(ene)
		frappe.set_user(self.bea)
		self.assertEqual(self.inhoud(self.detail), b"detail")
		self.assertEqual(foutcode(self, frappe.PermissionError, api.download, bestand=self.plan), "deling_ingetrokken")


class TestQuotumEnMapregels(GedeeldBasis):
	def test_quotum_van_de_ruimte_geldt(self):
		self.deel("Opslagmap", self.tekeningen, "schrijven")
		frappe.db.set_value("Ruimte", self.ruimte, "quotum_gb", 0.000_000_02)  # 20 bytes; er staat al 17
		self.assertEqual(
			foutcode(self, frappe.ValidationError, upload, self.ruimte, "groot.txt", b"te veel", map_naam=self.tekeningen),
			"quotum_ruimte_vol",
		)

	def test_mapregels_gelden(self):
		deling = self.deel("Opslagmap", self.tekeningen, "schrijven")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.append("mapregels", {"map": self.tekeningen, "pad": "Tekeningen", "extensies": "pdf, dwg"})
		doc.append("mapregels", {"map": self.details, "pad": "Tekeningen/Details", "alleen_lezen": 1})
		doc.save()
		frappe.set_user(self.bea)
		self.assertEqual(
			foutcode(self, frappe.ValidationError, upload, self.ruimte, "notitie.txt", b"x", map_naam=self.tekeningen),
			"extensie_niet_toegestaan",
		)
		self.assertEqual(
			foutcode(self, frappe.PermissionError, upload, self.ruimte, "d.dwg", b"x", map_naam=self.details), "map_alleen_lezen"
		)
		self.assertEqual(api.maak_mappen(deling=deling, paden=["Details/Sub"])["fouten"]["Details/Sub"]["foutcode"], "map_alleen_lezen")
		self.assertEqual(upload(self.ruimte, "schets.pdf", b"ok", map_naam=self.tekeningen)["versie"], 1)


class TestMelding(GedeeldBasis):
	def test_ontvanger_krijgt_mail_en_melding_met_link(self):
		frappe.set_user(self.anna)
		with patch("frappe.sendmail") as sendmail:
			deling = api.deel("Opslagmap", self.tekeningen, soort="gebruiker", gebruiker=self.bea, rechten="schrijven")["deling"]
		kwargs = sendmail.call_args.kwargs
		self.assertEqual(kwargs["recipients"], [self.bea])
		self.assertEqual(kwargs["subject"], "anna heeft de map Tekeningen met je gedeeld")
		self.assertIn(f"/opslag?deling={deling}", kwargs["message"])
		self.assertIn("bekijken en bewerken", kwargs["message"])
		self.assertEqual((kwargs["reference_doctype"], kwargs["reference_name"]), ("Opslagdeling", deling))
		melding = frappe.db.get_value(
			"Notification Log", {"for_user": self.bea}, ["subject", "link", "document_type", "document_name", "from_user"], as_dict=True
		)
		self.assertEqual(melding.subject, "anna heeft de map Tekeningen met je gedeeld")
		self.assertEqual((melding.link, melding.document_type, melding.document_name), (f"/opslag?deling={deling}", "Opslagmap", self.tekeningen))
		self.assertEqual(melding.from_user, self.anna)

	def test_bij_intrekken_en_bij_een_link_geen_mail(self):
		frappe.set_user(self.anna)
		with patch("frappe.sendmail"):
			deling = api.deel("Opslagbestand", self.plan, soort="gebruiker", gebruiker=self.bea)["deling"]
		with patch("frappe.sendmail") as sendmail:
			api.trek_in(deling)
			api.deel("Opslagmap", self.tekeningen, soort="link")
		sendmail.assert_not_called()
		self.assertEqual(frappe.db.count("Notification Log", {"for_user": self.bea}), 1)

	def test_zonder_uitgaande_mail_blijft_de_deling_staan(self):
		frappe.set_user(self.anna)
		frappe.db.delete("Error Log", {"method": "Mail over een deling niet verstuurd"})
		with patch("frappe.sendmail", side_effect=frappe.OutgoingEmailError("geen uitgaande mail")):
			deling = api.deel("Opslagmap", self.tekeningen, soort="gebruiker", gebruiker=self.bea)["deling"]
		self.assertTrue(frappe.db.exists("Opslagdeling", deling))
		self.assertTrue(frappe.db.exists("Error Log", {"method": "Mail over een deling niet verstuurd"}))
		self.assertEqual(frappe.db.count("Notification Log", {"for_user": self.bea}), 1)

	def test_zonder_nagebootste_mail_ook_geen_fout(self):
		# De echte mail van de site (in de tests zonder uitgaand account): de deling lukt altijd.
		frappe.set_user(self.anna)
		deling = api.deel("Opslagmap", self.tekeningen, soort="gebruiker", gebruiker=self.bea)["deling"]
		self.assertTrue(frappe.db.exists("Opslagdeling", deling))
