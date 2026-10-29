"""Projectdossier: dossiergegevens op bestanden, revisies per versie en de berekende map Actueel (dossier.py)."""

import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage import dossier
from openaec_storage.tests.hulp import NagebootsteS3, foutcode, gebruiker, ruim_op, upload


class TestRevisies(IntegrationTestCase):
	def test_volgorde_en_volgende(self):
		self.assertLess(dossier.revisie_sleutel(None), dossier.revisie_sleutel("0"))
		self.assertLess(dossier.revisie_sleutel("9"), dossier.revisie_sleutel("A"))
		self.assertLess(dossier.revisie_sleutel("B"), dossier.revisie_sleutel("C"))
		self.assertLess(dossier.revisie_sleutel("Z"), dossier.revisie_sleutel("AA"))
		self.assertEqual(dossier.volgende_revisie(None), "A")
		self.assertEqual(dossier.volgende_revisie("A"), "B")
		self.assertEqual(dossier.volgende_revisie("z"), "AA")
		self.assertEqual(dossier.volgende_revisie("AZ"), "BA")
		self.assertEqual(dossier.volgende_revisie("3"), "4")

	def test_actueel_regel(self):
		def r(code, rev, status, volgnummer=1, bestand="b"):
			return frappe._dict(documentcode=code, revisie=rev, revisiestatus=status, volgnummer=volgnummer, bestand=bestand)

		uit = {
			a.documentcode: a
			for a in dossier.bereken_actueel(
				[
					# Hoogste vrijgegeven revisie wint, ook als er een nieuwere in concept is.
					r("X", "A", "vrijgegeven", 1),
					r("X", "B", "vrijgegeven", 2),
					r("X", "C", "concept", 3),
					# Niets vrijgegeven: de nieuwste die niet vervallen is, voorlopig.
					r("Y", "A", "concept", 1),
					r("Y", "B", "ter_beoordeling", 2),
					r("Y", "C", "vervallen", 3),
					# Alles vervallen: niet in Actueel.
					r("Z", "A", "vervallen", 1),
				]
			)
		}
		self.assertEqual((uit["X"].revisie, uit["X"].voorlopig), ("B", False))
		self.assertEqual(uit["X"].revisies, ["A", "B", "C"])
		self.assertEqual((uit["Y"].revisie, uit["Y"].voorlopig), ("B", True))
		self.assertNotIn("Z", uit)

	def test_instellingen_per_omgeving(self):
		oud = frappe.local.conf.get("openaec_dossier")
		try:
			frappe.local.conf["openaec_dossier"] = {"fasen": [{"code": "ow", "naam": "Ontwerp"}, "UITV"], "mappen": {"fotos": "Beeld"}}
			i = dossier.instellingen()
			self.assertEqual([f["code"] for f in i["fasen"]], ["OW", "UITV"])
			self.assertEqual(i["mappen"]["fotos"], "Beeld")
			self.assertEqual(i["mappen"]["fases"], "Fases")
			self.assertEqual([d["code"] for d in i["disciplines"]], ["AR", "CO", "IN", "IF", "AL"])
		finally:
			if oud is None:
				frappe.local.conf.pop("openaec_dossier", None)
			else:
				frappe.local.conf["openaec_dossier"] = oud

	def test_mapnamen_zonder_slash(self):
		paden = [p for p, *_ in dossier.dossiermappen()]
		self.assertIn("Fases/DO/Constructie", paden)
		self.assertIn("Fases/TO (bestek)/Installaties (W-E)", paden)
		self.assertIn("3D-model", paden)
		self.assertIn("Foto's", paden)
		self.assertIn("Correspondentie", paden)
		self.assertNotIn("Actueel", paden)


class TestDossier(IntegrationTestCase):
	def setUp(self):
		self.s3 = NagebootsteS3().start()
		frappe.set_user("Administrator")
		frappe.db.delete("Mapsjabloon", {"name": dossier.DOSSIERSJABLOON})
		dossier.zorg_voor_dossiersjabloon()
		self.schrijver = gebruiker("schrijver")
		self.lezer = gebruiker("lezer")
		self.buiten = gebruiker("buiten")
		self.ruimte = (
			frappe.get_doc(
				{
					"doctype": "Ruimte",
					"naam": "Brug",
					"soort": "project",
					"mapsjabloon": dossier.DOSSIERSJABLOON,
					"leden": [{"gebruiker": self.schrijver, "rol": "schrijver"}, {"gebruiker": self.lezer, "rol": "lezer"}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.co_do = dossier.map_op_pad(self.ruimte, ["Fases", "DO", "Constructie"], maak=False)
		self.co_vo = dossier.map_op_pad(self.ruimte, ["Fases", "VO", "Constructie"], maak=False)

	def tearDown(self):
		frappe.set_user("Administrator")
		ruim_op()
		self.s3.stop()

	def test_sjabloon_legt_de_structuur_aan(self):
		self.assertTrue(self.co_do and self.co_vo)
		self.assertTrue(dossier.map_op_pad(self.ruimte, ["3D-model"], maak=False))
		self.assertTrue(dossier.map_op_pad(self.ruimte, ["Foto's"], maak=False))
		self.assertFalse(dossier.map_op_pad(self.ruimte, ["Actueel"], maak=False))

	def test_voorstel_uit_map_en_naam(self):
		frappe.set_user(self.schrijver)
		b = upload(self.ruimte, "Ligger rev B.pdf", b"%PDF-1", self.co_do)["bestand"]
		v = dossier.eigenschappen(b)["voorstel"]
		self.assertEqual((v["fase"], v["discipline"], v["soort"], v["revisie"]), ("DO", "CO", "TEK", "B"))
		self.assertEqual(v["documentcode"], "B-CO-TEK-001")

	def test_zetten_en_actueel_met_revisies(self):
		frappe.set_user(self.schrijver)
		a = upload(self.ruimte, "Ligger rev A.pdf", b"%PDF-A", self.co_vo)["bestand"]
		dossier.zet_eigenschappen([a], {"documentcode": "AUTO", "discipline": "CO", "soort": "TEK", "fase": "VO", "revisie": "A", "status": "vrijgegeven"})
		code = frappe.db.get_value("Opslagbestand", a, "documentcode")
		b = upload(self.ruimte, "Ligger rev B.pdf", b"%PDF-B", self.co_do)["bestand"]
		# De volgende revisie krijgt dezelfde code (dezelfde naam zonder de revisie).
		self.assertEqual(dossier.eigenschappen(b)["voorstel"]["documentcode"], code)
		dossier.zet_eigenschappen([b], {"documentcode": code, "discipline": "CO", "soort": "TEK", "fase": "DO", "revisie": "B", "status": "ter_beoordeling"})
		uit = dossier.actueel(self.ruimte)
		doc = uit["groepen"][0]["documenten"][0]
		self.assertEqual((doc["bestand"], doc["revisie"], doc["voorlopig"]), (a, "A", False))
		self.assertEqual(doc["revisies"], ["A", "B"])
		# Rev B vrijgegeven: die vervangt A vanzelf in Actueel.
		dossier.zet_eigenschappen([b], {"status": "vrijgegeven"})
		doc = dossier.actueel(self.ruimte)["groepen"][0]["documenten"][0]
		self.assertEqual((doc["bestand"], doc["revisie"]), (b, "B"))
		self.assertEqual(dossier.actueel(self.ruimte)["groepen"][0]["discipline_naam"], "Constructie")
		# Dezelfde code en revisie op een ander bestand kan niet.
		c = upload(self.ruimte, "Kopie.pdf", b"%PDF-C", self.co_do)["bestand"]
		self.assertEqual(
			foutcode(self, frappe.ValidationError, dossier.zet_eigenschappen, [c], {"documentcode": code, "revisie": "B"}),
			"dossier_revisie_bestaat",
		)

	def test_nieuwe_versie_is_weer_concept_maar_actueel_blijft_vrijgegeven(self):
		frappe.set_user(self.schrijver)
		b = upload(self.ruimte, "Plattegrond.pdf", b"%PDF-1", self.co_do)["bestand"]
		dossier.zet_eigenschappen([b], {"documentcode": "AUTO", "revisie": "A", "status": "vrijgegeven", "discipline": "CO", "soort": "TEK"})
		upload(self.ruimte, "Plattegrond.pdf", b"%PDF-2", self.co_do, basis_versie=1)
		self.assertEqual(frappe.db.get_value("Opslagbestand", b, ["revisie", "revisiestatus"]), ("A", "concept"))
		doc = dossier.actueel(self.ruimte)["groepen"][0]["documenten"][0]
		self.assertEqual((doc["versie"], doc["huidige_versie"], doc["status"]), (1, 2, "vrijgegeven"))
		# Als nieuwe revisie: B, concept; na vrijgeven staat versie 2 in Actueel.
		uit = dossier.nieuwe_revisie(b)
		self.assertEqual((uit["revisie"], uit["status"]), ("B", "concept"))
		dossier.zet_eigenschappen([b], {"status": "vrijgegeven"})
		doc = dossier.actueel(self.ruimte)["groepen"][0]["documenten"][0]
		self.assertEqual((doc["versie"], doc["revisie"]), (2, "B"))
		versies = {v["volgnummer"]: (v["revisie"], v["status"]) for v in dossier.eigenschappen(b)["versies"]}
		self.assertEqual(versies, {1: ("A", "vrijgegeven"), 2: ("B", "vrijgegeven")})

	def test_nieuwe_code_volgt_de_gekozen_discipline_en_soort(self):
		frappe.set_user(self.schrijver)
		b = upload(self.ruimte, "Berekening ligger.pdf", b"%PDF", self.co_do)["bestand"]
		dossier.zet_eigenschappen([b], {"documentcode": "AUTO", "discipline": "CO", "soort": "RAP"})
		self.assertEqual(frappe.db.get_value("Opslagbestand", b, "documentcode"), "B-CO-RAP-001")

	def test_vaste_volgorde_van_fases_en_disciplines(self):
		fases = dossier.map_op_pad(self.ruimte, ["Fases"], maak=False)
		do = dossier.map_op_pad(self.ruimte, ["Fases", "DO"], maak=False)
		volgorde = dossier.mapvolgorde(self.ruimte, fases)
		self.assertEqual(sorted(volgorde, key=volgorde.get), ["so", "vo", "do", "to (bestek)", "uo", "oplevering"])
		volgorde = dossier.mapvolgorde(self.ruimte, do)
		self.assertEqual(sorted(volgorde, key=volgorde.get), ["architectuur", "constructie", "installaties (w-e)", "infra", "algemeen"])
		self.assertEqual(dossier.mapvolgorde(self.ruimte, None), {})
		self.assertEqual(dossier.mapvolgorde(self.ruimte, self.co_do), {})

	def test_alleen_voorlopig_als_niets_vrijgegeven(self):
		frappe.set_user(self.schrijver)
		b = upload(self.ruimte, "Schets.pdf", b"%PDF", self.co_vo)["bestand"]
		dossier.zet_eigenschappen([b], {"documentcode": "AUTO", "revisie": "A", "status": "concept"})
		uit = dossier.actueel(self.ruimte)
		self.assertEqual((uit["aantal"], uit["voorlopig"]), (1, 1))
		dossier.zet_eigenschappen([b], {"status": "vervallen"})
		self.assertEqual(dossier.actueel(self.ruimte)["aantal"], 0)

	def test_bulk_en_validatie(self):
		frappe.set_user(self.schrijver)
		ids = [upload(self.ruimte, f"Blad {n}.pdf", b"%PDF", self.co_do)["bestand"] for n in range(3)]
		uit = dossier.zet_eigenschappen(ids, {"documentcode": "auto", "fase": "do", "discipline": "CO"})
		self.assertEqual(uit["aantal"], 3)
		codes = {frappe.db.get_value("Opslagbestand", i, "documentcode") for i in ids}
		self.assertEqual(len(codes), 3)
		self.assertEqual(foutcode(self, frappe.ValidationError, dossier.zet_eigenschappen, ids, {"documentcode": "X-1"}), "dossier_code_bulk")
		self.assertEqual(foutcode(self, frappe.ValidationError, dossier.zet_eigenschappen, ids[:1], {"fase": "XX"}), "dossier_onbekende_waarde")
		self.assertEqual(foutcode(self, frappe.ValidationError, dossier.zet_eigenschappen, ids[:1], {"revisie": "B-2"}), "dossier_ongeldige_revisie")
		self.assertEqual(foutcode(self, frappe.ValidationError, dossier.zet_eigenschappen, ids[:1], {"status": "klaar"}), "dossier_onbekende_waarde")

	def test_rechten(self):
		frappe.set_user(self.schrijver)
		b = upload(self.ruimte, "Ligger.pdf", b"%PDF", self.co_do)["bestand"]
		dossier.zet_eigenschappen([b], {"documentcode": "AUTO", "status": "vrijgegeven"})
		frappe.set_user(self.lezer)
		self.assertEqual(dossier.actueel(self.ruimte)["aantal"], 1)
		self.assertFalse(dossier.eigenschappen(b)["mag_wijzigen"])
		with self.assertRaises(frappe.PermissionError):
			dossier.zet_eigenschappen([b], {"status": "vervallen"})
		frappe.set_user(self.buiten)
		with self.assertRaises(frappe.PermissionError):
			dossier.actueel(self.ruimte)
		with self.assertRaises(frappe.PermissionError):
			dossier.eigenschappen(b)

	def test_hoofdmodel(self):
		frappe.set_user(self.schrijver)
		model_map = dossier.map_op_pad(self.ruimte, ["3D-model"], maak=False)
		pdf = upload(self.ruimte, "Ligger.pdf", b"%PDF", self.co_do)["bestand"]
		self.assertEqual(foutcode(self, frappe.ValidationError, dossier.zet_hoofdmodel, pdf), "dossier_geen_model")
		m1 = upload(self.ruimte, "Brug v1.ifc", b"ISO-10303-21;", model_map)["bestand"]
		m2 = upload(self.ruimte, "Brug v2.ifc", b"ISO-10303-21;", model_map)["bestand"]
		dossier.zet_hoofdmodel(m1)
		dossier.zet_hoofdmodel(m2)
		self.assertEqual(frappe.db.get_value("Opslagbestand", m1, "hoofdmodel"), 0)
		model = dossier.hoofdmodel(self.ruimte)["model"]
		self.assertEqual((model["bestand"], model["versie"], model["voorlopig"]), (m2, 1, True))
		self.assertTrue(dossier.overzicht(self.ruimte)["dossier"])
		frappe.set_user(self.lezer)
		with self.assertRaises(frappe.PermissionError):
			dossier.zet_hoofdmodel(m1)

	def test_bewaar_bytes_volgt_mapregels(self):
		fotos = dossier.map_op_pad(self.ruimte, ["Foto's"], maak=False)
		frappe.set_user(self.lezer)
		with self.assertRaises(frappe.PermissionError):
			dossier.bewaar_bytes(self.ruimte, fotos, "a.jpg", b"jpg")
		# Zonder rechtencontrole (spiegelen door de server) wel, met herkomst; de extensieregel blijft gelden.
		uit = dossier.bewaar_bytes(self.ruimte, fotos, "a.jpg", b"jpg", controleer_rechten=False, herkomst="File:x")
		self.assertEqual(frappe.db.get_value("Opslagbestand", uit["bestand"], "herkomst"), "File:x")
		self.assertEqual(
			foutcode(self, frappe.ValidationError, dossier.bewaar_bytes, self.ruimte, fotos, "a.txt", b"t", controleer_rechten=False),
			"extensie_niet_toegestaan",
		)
