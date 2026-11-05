"""Vergelijk-en-schrijf bij uploaden (ontwerp samenwerken, stap 4): vastleggen onder een slot, `bij_conflict` en
`samengevoegd_uit`.

`TestGelijktijdig` laat twee echte databaseverbindingen in een vaste volgorde wisselen: elk verzoek draait in een
eigen thread met een eigen Frappe-context en databaseverbinding, zoals twee verzoeken op twee werkers. Wat de andere
verbinding moet zien, is echt gecommit; die tests ruimen hun gegevens zelf op."""

import json
import threading
import time
from unittest import mock

import frappe
import requests
from frappe.tests import IntegrationTestCase

from openaec_storage import api, s3
from openaec_storage.patches import huidige_versie_id_invullen
from openaec_storage.tests.hulp import (
	OPSLAGINSTELLINGEN,
	NagebootsteS3,
	foutcode,
	gebruiker,
	nummer_wijzigingen,
	ruim_op,
	ruimte,
	upload,
)

WACHT = 30  # seconden: hoogstens zo lang wachten op een thread of een sein
BEDENKTIJD = 1.0  # seconden: zo lang krijgt B om bij het slot te komen


def start(ruimte_naam: str, naam: str, inhoud: bytes, **extra) -> dict:
	"""`upload_start` en de delen naar S3, zoals een client; geeft de parameters voor `upload_klaar`."""
	begin = api.upload_start(ruimte=ruimte_naam, naam=naam, grootte=len(inhoud), **extra)
	delen, grootte = [], begin["deelgrootte"]
	for deel in begin["delen"]:
		antwoord = requests.put(deel["url"], data=inhoud[(deel["nummer"] - 1) * grootte : deel["nummer"] * grootte], timeout=30)
		antwoord.raise_for_status()
		delen.append({"nummer": deel["nummer"], "etag": antwoord.headers["ETag"]})
	return {"upload": begin["upload"], "delen": delen}


def sleutel_van(upload: dict) -> str:
	return frappe.cache.get_value(api.UPLOAD_SLEUTEL + upload["upload"])["sleutel"]


def item(naam: str, inhoud: bytes, **extra) -> dict:
	return {"naam": naam, "grootte": len(inhoud), "blokhash": "h-" + naam, **extra}


class Hulp:
	"""Controles die beide testklassen gebruiken."""

	def volgnummers(self, bestand: str) -> list[int]:
		return [
			r[0]
			for r in frappe.db.sql(
				"select volgnummer from `tabBestandsversie` where bestand = %s order by volgnummer", bestand
			)
		]

	def actief(self, naam: str, map: str | None = None) -> list[str]:
		"""Actieve bestanden op de plek waarvan de naam zo begint (de naam zelf en conflictkopieën)."""
		stam = naam.rsplit(".", 1)[0]
		return sorted(
			frappe.get_all(
				"Opslagbestand",
				filters={
					"ruimte": self.ruimte,
					"map": map or ("is", "not set"),
					"naam": ("like", stam + "%"),
					"verwijderd_op": ("is", "not set"),
				},
				pluck="naam",
				ignore_permissions=True,
			)
		)

	def inhoud(self, bestand: str, versie: int | None = None) -> bytes:
		return requests.get(api.download(bestand=bestand, versie=versie)["url"], timeout=30).content

	def bestaat_in_s3(self, sleutel: str) -> bool:
		try:
			s3.grootte(sleutel)
			return True
		except Exception:
			return False

	def aantal_in_s3(self) -> int:
		import boto3

		from openaec_storage.tests.hulp import BUCKET, REGIO

		return boto3.client("s3", region_name=REGIO).list_objects_v2(Bucket=BUCKET).get("KeyCount", 0)


class TestVergelijkEnSchrijf(Hulp, IntegrationTestCase):
	"""Het gedrag van `bij_conflict` en `samengevoegd_uit` langs `upload_start`/`upload_klaar` en `upload_klein`."""

	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagvergrendeling")
		self.s3 = NagebootsteS3().start()
		self.anna = gebruiker("anna")
		self.piet = gebruiker("piet")
		self.ruimte = ruimte("Project Vergelijk", {self.anna: "schrijver", self.piet: "schrijver"})
		frappe.set_user(self.anna)
		frappe.local.response.pop("foutcode", None)
		frappe.local.response.pop("huidige_versie", None)
		frappe.local.response.pop("huidige_versie_id", None)
		frappe.local.response.pop("huidig_bestand", None)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.form_dict = frappe._dict()
		frappe.local.response.pop("huidige_versie", None)
		frappe.local.response.pop("huidige_versie_id", None)
		frappe.local.response.pop("huidig_bestand", None)
		frappe.db.delete("Opslagvergrendeling")
		self.s3.stop()
		ruim_op()

	def huidige_versie_in_antwoord(self):
		return frappe.local.response.pop("huidige_versie", None)

	def basis_in_antwoord(self) -> tuple:
		"""`huidige_versie` en `huidig_bestand` uit het antwoord; beide moeten er staan (ook als ze null zijn)."""
		huidige_versie, _id, huidig_bestand = self.basis_id_in_antwoord()
		return huidige_versie, huidig_bestand

	def basis_id_in_antwoord(self) -> tuple:
		"""`huidige_versie`, `huidige_versie_id` en `huidig_bestand` uit het antwoord; alle drie moeten er staan."""
		sleutels = ("huidige_versie", "huidige_versie_id", "huidig_bestand")
		for sleutel in sleutels:
			self.assertIn(sleutel, frappe.local.response)
		return tuple(frappe.local.response.pop(sleutel) for sleutel in sleutels)

	def vervangen_door_piet(self, naam: str = "planning.ifc") -> tuple[str, str]:
		"""Het tegenvoorbeeld: Anna's bestand (F1, versie 1); Piet verwijdert F1 en maakt onder dezelfde naam een
		nieuw bestand (F2, versie 1)."""
		f1 = upload(self.ruimte, naam, b"van anna")["bestand"]
		f2 = self.piet_vervangt(f1, naam)
		return f1, f2

	def piet_vervangt(self, f1: str, naam: str = "planning.ifc") -> str:
		frappe.set_user(self.piet)
		try:
			api.verwijder(bestand=f1)
			return upload(self.ruimte, naam, b"van piet")["bestand"]
		finally:
			frappe.set_user(self.anna)

	def drie_versies(self, naam: str = "plan.ifc") -> str:
		"""Versie 1 van Anna, 2 en 3 van Piet."""
		bestand = upload(self.ruimte, naam, b"een")["bestand"]
		frappe.set_user(self.piet)
		upload(self.ruimte, naam, b"twee", basis_versie=1)
		upload(self.ruimte, naam, b"drie", basis_versie=2)
		frappe.set_user(self.anna)
		return bestand

	# --- mogelijkheden en de standaard -------------------------------------------------------------------

	def test_mogelijkheden_noemen_vergelijk_en_schrijf(self):
		m = api.mogelijkheden()
		self.assertIs(m["bij_conflict_weiger"], True)
		self.assertIs(m["samengevoegd_uit"], True)
		self.assertIs(m["basis_bestand"], True)

	def test_zonder_bij_conflict_blijft_een_verouderde_basis_een_conflictkopie(self):
		bestand = upload(self.ruimte, "plan.ifc", b"een")["bestand"]
		upload(self.ruimte, "plan.ifc", b"twee", basis_versie=1)
		zonder = api.upload_klaar(**start(self.ruimte, "plan.ifc", b"drie", basis_versie=1))
		met_kopie = api.upload_klaar(**start(self.ruimte, "plan.ifc", b"vier", basis_versie=1, bij_conflict="kopie"))
		for r in (zonder, met_kopie):
			self.assertTrue(r["conflict"])
			self.assertIn("(conflict", r["naam"])
			self.assertEqual(r["versie"], 1)
		self.assertEqual(self.volgnummers(bestand), [1, 2])
		self.assertEqual(len(self.actief("plan.ifc")), 3)

	def test_onbekende_bij_conflict_wordt_geweigerd(self):
		with self.assertRaises(frappe.ValidationError):
			start(self.ruimte, "plan.ifc", b"x", bij_conflict="overschrijf")

	# --- weiger langs upload_start/upload_klaar ----------------------------------------------------------

	def test_weiger_met_de_juiste_basis_geeft_een_nieuwe_versie(self):
		bestand = upload(self.ruimte, "plan.ifc", b"een")["bestand"]
		r = api.upload_klaar(**start(self.ruimte, "plan.ifc", b"twee", basis_versie=1, bij_conflict="weiger"))
		self.assertEqual((r["bestand"], r["versie"], r["conflict"]), (bestand, 2, False))

	def test_weiger_voor_een_nieuw_bestand_maakt_het_gewoon_aan(self):
		r = api.upload_klaar(**start(self.ruimte, "nieuw.ifc", b"een", bij_conflict="weiger"))
		self.assertEqual((r["versie"], r["conflict"]), (1, False))

	def test_weiger_bij_upload_start_als_de_basis_al_verouderd_is(self):
		bestand = upload(self.ruimte, "plan.ifc", b"een")["bestand"]
		upload(self.ruimte, "plan.ifc", b"twee", basis_versie=1)
		code = foutcode(self, api.VerouderdeBasis, start, self.ruimte, "plan.ifc", b"x", basis_versie=1, bij_conflict="weiger")
		self.assertEqual(code, "verouderde_basis")
		self.assertEqual(self.huidige_versie_in_antwoord(), 2)
		self.assertEqual(api.VerouderdeBasis.http_status_code, 409)
		# Zonder basis voor een bestand dat er al is: ook verouderd (de client dacht dat het nieuw was).
		code = foutcode(self, api.VerouderdeBasis, start, self.ruimte, "plan.ifc", b"x", bij_conflict="weiger")
		self.assertEqual(code, "verouderde_basis")
		self.assertEqual(self.huidige_versie_in_antwoord(), 2)
		self.assertEqual(self.volgnummers(bestand), [1, 2])
		self.assertEqual(self.actief("plan.ifc"), ["plan.ifc"])

	def test_weiger_bij_upload_klaar_ruimt_de_upload_op_en_maakt_geen_kopie(self):
		bestand = upload(self.ruimte, "plan.ifc", b"een")["bestand"]
		frappe.set_user(self.piet)
		mijn = start(self.ruimte, "plan.ifc", b"van piet", basis_versie=1, bij_conflict="weiger")
		sleutel = sleutel_van(mijn)
		frappe.set_user(self.anna)
		upload(self.ruimte, "plan.ifc", b"van anna", basis_versie=1)
		frappe.set_user(self.piet)
		self.assertEqual(foutcode(self, api.VerouderdeBasis, api.upload_klaar, **mijn), "verouderde_basis")
		self.assertEqual(self.huidige_versie_in_antwoord(), 2)
		self.assertFalse(self.bestaat_in_s3(sleutel), "het object van de geweigerde upload staat nog in de opslag")
		self.assertIsNone(frappe.cache.get_value(api.UPLOAD_SLEUTEL + mijn["upload"], use_local_cache=False))
		self.assertEqual(self.volgnummers(bestand), [1, 2])
		self.assertEqual(self.actief("plan.ifc"), ["plan.ifc"])
		self.assertEqual(self.inhoud(bestand), b"van anna")

	def test_weiger_met_een_slot_van_een_ander(self):
		bestand = upload(self.ruimte, "plan.ifc", b"een")["bestand"]
		frappe.set_user(self.piet)
		voor_het_slot = start(self.ruimte, "plan.ifc", b"van piet", basis_versie=1, bij_conflict="weiger")
		sleutel = sleutel_van(voor_het_slot)
		frappe.set_user(self.anna)
		api.vergrendel(bestand=bestand)
		frappe.set_user(self.piet)
		# Al bij het begin, ook als de basis daarnaast verouderd is: een slot gaat voor.
		for basis in (1, 0):
			code = foutcode(
				self, frappe.PermissionError, start, self.ruimte, "plan.ifc", b"x", basis_versie=basis, bij_conflict="weiger"
			)
			self.assertEqual(code, "bestand_vergrendeld")
		# En bij het afronden, als het slot er na het begin kwam.
		self.assertEqual(foutcode(self, frappe.PermissionError, api.upload_klaar, **voor_het_slot), "bestand_vergrendeld")
		self.assertIsNone(self.huidige_versie_in_antwoord())
		self.assertFalse(self.bestaat_in_s3(sleutel))
		self.assertEqual(self.volgnummers(bestand), [1])
		self.assertEqual(self.actief("plan.ifc"), ["plan.ifc"])
		# Zonder weiger blijft het een conflictkopie, zoals altijd.
		r = api.upload_klaar(**start(self.ruimte, "plan.ifc", b"van piet", basis_versie=1))
		self.assertTrue(r["conflict"])

	def test_weiger_terwijl_het_bestand_weg_is_maakt_een_nieuw_bestand(self):
		oud = upload(self.ruimte, "plan.ifc", b"een")["bestand"]
		mijn = start(self.ruimte, "plan.ifc", b"twee", basis_versie=1, bij_conflict="weiger")
		api.verwijder(bestand=oud)
		r = api.upload_klaar(**mijn)
		self.assertNotEqual(r["bestand"], oud)
		self.assertEqual((r["versie"], r["conflict"]), (1, False))

	# --- weiger in upload_klein --------------------------------------------------------------------------

	def test_upload_klein_met_bij_conflict_per_bestand(self):
		a = upload(self.ruimte, "a.txt", b"a1")["bestand"]
		upload(self.ruimte, "a.txt", b"a2", basis_versie=1)
		b = upload(self.ruimte, "b.txt", b"b1")["bestand"]
		c = upload(self.ruimte, "c.txt", b"c1")["bestand"]
		frappe.set_user(self.piet)
		api.vergrendel(bestand=c)
		frappe.set_user(self.anna)
		voor = self.aantal_in_s3()
		bestanden = [
			(item("a.txt", b"x", basis_versie=1, bij_conflict="weiger"), b"x"),
			(item("a.txt", b"y", basis_versie=1), b"y"),
			(item("b.txt", b"b2", basis_versie=1, bij_conflict="weiger"), b"b2"),
			(item("c.txt", b"c2", basis_versie=1, bij_conflict="weiger"), b"c2"),
			(item("d.txt", b"d", bij_conflict="weiger"), b"d"),
			(item("e.txt", b"e", bij_conflict="overschrijf"), b"e"),
		]
		r = api.upload_klein_bytes(self.ruimte, [i for i, _ in bestanden], {f"f{n}": d for n, (_, d) in enumerate(bestanden)})[
			"resultaten"
		]
		self.assertEqual(
			[(x["ok"], x.get("foutcode"), x.get("huidige_versie")) for x in r],
			[
				(False, "verouderde_basis", 2),
				(True, None, None),
				(True, None, None),
				(False, "bestand_vergrendeld", None),
				(True, None, None),
				(False, None, None),
			],
		)
		self.assertTrue(r[1]["conflict"])
		self.assertEqual((r[2]["bestand"], r[2]["versie"]), (b, 2))
		self.assertEqual(r[4]["versie"], 1)
		self.assertEqual(self.volgnummers(a), [1, 2])
		self.assertEqual(self.volgnummers(c), [1])
		self.assertNotIn("huidige_versie", frappe.local.response)
		self.assertNotIn("huidig_bestand", frappe.local.response)
		# Alleen wat vastgelegd is, staat in de opslag: de kopie, b.txt v2 en d.txt.
		self.assertEqual(self.aantal_in_s3(), voor + 3)

	# --- samengevoegd_uit --------------------------------------------------------------------------------

	def test_samengevoegd_uit_komt_op_de_nieuwe_versie(self):
		bestand = upload(self.ruimte, "plan.ifc", b"een")["bestand"]
		frappe.set_user(self.piet)
		upload(self.ruimte, "plan.ifc", b"twee", basis_versie=1)
		frappe.set_user(self.anna)
		nummer_wijzigingen()
		sinds = api.wijzigingen(ruimte=self.ruimte)["cursor"]
		r = api.upload_klaar(
			**start(
				self.ruimte, "plan.ifc", b"een+twee", basis_versie=2, samengevoegd_uit='{"basis": 1, "hun": 2}', bij_conflict="weiger"
			)
		)
		self.assertEqual((r["versie"], r["conflict"]), (3, False))
		self.assertEqual(
			[(v.volgnummer, v.samengevoegd_uit) for v in api.versies(bestand=bestand)],
			[(3, {"basis": 1, "hun": 2}), (2, None), (1, None)],
		)
		# In de wijzigingenlijst een gewone nieuwe versie: bestaande clients filteren op `gewijzigd`.
		nummer_wijzigingen()
		regels = api.wijzigingen(ruimte=self.ruimte, sinds=sinds)["wijzigingen"]
		self.assertEqual([(w["soort"], w["doel"]) for w in regels], [("gewijzigd", bestand)])

	def test_samengevoegd_uit_in_upload_klein(self):
		bestand = upload(self.ruimte, "plan.txt", b"een")["bestand"]
		upload(self.ruimte, "plan.txt", b"twee", basis_versie=1)
		(r,) = api.upload_klein_bytes(
			self.ruimte,
			[item("plan.txt", b"drie", basis_versie=2, samengevoegd_uit={"basis": 1, "hun": 2}, bij_conflict="weiger")],
			{"f0": b"drie"},
		)["resultaten"]
		self.assertTrue(r["ok"], r)
		self.assertEqual(api.versies(bestand=bestand)[0].samengevoegd_uit, {"basis": 1, "hun": 2})

	def test_ongeldige_samengevoegd_uit_wordt_geweigerd(self):
		self.drie_versies()
		gevallen = [
			("geen JSON", "geen json", 3),
			("ontbrekende sleutel", '{"basis": 1}', 3),
			("extra sleutel", '{"basis": 1, "hun": 3, "rapport": 1}', 3),
			("tekst in plaats van getal", '{"basis": "1", "hun": 3}', 3),
			("kommagetal", '{"basis": 1.0, "hun": 3}', 3),
			("waar/onwaar", '{"basis": true, "hun": 3}', 3),
			("basis niet kleiner dan hun", '{"basis": 3, "hun": 3}', 3),
			("basis nul", '{"basis": 0, "hun": 3}', 3),
			("hun is niet de basis_versie", '{"basis": 1, "hun": 2}', 3),
			("zonder basis_versie", '{"basis": 1, "hun": 2}', None),
			("versie bestaat niet", '{"basis": 2, "hun": 4}', 4),
		]
		for omschrijving, waarde, basis in gevallen:
			with self.subTest(omschrijving):
				code = foutcode(
					self, frappe.ValidationError, start, self.ruimte, "plan.ifc", b"x", basis_versie=basis, samengevoegd_uit=waarde
				)
				self.assertEqual(code, "ongeldige_samenvoeging")
		with self.subTest("geen bestand op deze plek"):
			code = foutcode(
				self, frappe.ValidationError, start, self.ruimte, "ander.ifc", b"x", basis_versie=2, samengevoegd_uit='{"basis": 1, "hun": 2}'
			)
			self.assertEqual(code, "ongeldige_samenvoeging")
		with self.subTest("upload_klein, per bestand"):
			(r,) = api.upload_klein_bytes(
				self.ruimte, [item("plan.ifc", b"x", basis_versie=3, samengevoegd_uit={"basis": 3, "hun": 3})], {"f0": b"x"}
			)["resultaten"]
			self.assertEqual((r["ok"], r["foutcode"]), (False, "ongeldige_samenvoeging"))

	def test_samengevoegd_uit_niet_op_een_conflictkopie(self):
		self.drie_versies()
		r = api.upload_klaar(
			**start(self.ruimte, "plan.ifc", b"een+twee", basis_versie=2, samengevoegd_uit='{"basis": 1, "hun": 2}')
		)
		self.assertTrue(r["conflict"])
		self.assertEqual([v.samengevoegd_uit for v in api.versies(bestand=r["bestand"])], [None])

	# --- basis_bestand: een versienummer is alleen uniek binnen één bestand ------------------------------

	def test_zonder_basis_bestand_telt_alleen_het_versienummer_zoals_voorheen(self):
		"""Het tegenvoorbeeld zonder `basis_bestand`: Anna's oude inhoud wordt versie 2 van Piets nieuwe bestand,
		zonder conflict. Zo was het, en zo blijft het voor een client die `basis_bestand` niet meestuurt."""
		_f1, f2 = self.vervangen_door_piet()
		r = api.upload_klaar(**start(self.ruimte, "planning.ifc", b"oud van anna", basis_versie=1, bij_conflict="weiger"))
		self.assertEqual((r["bestand"], r["versie"], r["conflict"]), (f2, 2, False))
		_g1, g2 = self.vervangen_door_piet("rooster.txt")
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("rooster.txt", b"oud", basis_versie=1, bij_conflict="weiger")], {"f0": b"oud"}
		)["resultaten"]
		self.assertEqual((k["ok"], k["bestand"], k["versie"], k["conflict"]), (True, g2, 2, False))

	def test_basis_bestand_ander_bestand_met_weiger_bij_upload_start(self):
		f1, f2 = self.vervangen_door_piet()
		code = foutcode(
			self, api.VerouderdeBasis, start, self.ruimte, "planning.ifc", b"oud van anna", basis_versie=1, basis_bestand=f1, bij_conflict="weiger"
		)
		self.assertEqual(code, "verouderde_basis")
		self.assertEqual(self.basis_in_antwoord(), (1, f2))
		self.assertEqual(self.volgnummers(f2), [1])
		self.assertEqual(self.inhoud(f2), b"van piet")

	def test_basis_bestand_ander_bestand_met_weiger_bij_upload_klaar(self):
		"""Onder het slot: Anna begint terwijl F1 er nog staat, Piet vervangt het voordat zij afrondt."""
		f1 = upload(self.ruimte, "planning.ifc", b"van anna")["bestand"]
		mijn = start(self.ruimte, "planning.ifc", b"oud van anna", basis_versie=1, basis_bestand=f1, bij_conflict="weiger")
		sleutel = sleutel_van(mijn)
		f2 = self.piet_vervangt(f1)
		self.assertEqual(foutcode(self, api.VerouderdeBasis, api.upload_klaar, **mijn), "verouderde_basis")
		self.assertEqual(self.basis_in_antwoord(), (1, f2))
		self.assertFalse(self.bestaat_in_s3(sleutel), "het object van de geweigerde upload staat nog in de opslag")
		self.assertEqual(self.volgnummers(f2), [1])
		self.assertEqual(self.actief("planning.ifc"), ["planning.ifc"])
		self.assertEqual(self.inhoud(f2), b"van piet")

	def test_basis_bestand_ander_bestand_met_kopie_bij_upload_klaar(self):
		f1 = upload(self.ruimte, "planning.ifc", b"van anna")["bestand"]
		voor = start(self.ruimte, "planning.ifc", b"oud van anna", basis_versie=1, basis_bestand=f1)
		f2 = self.piet_vervangt(f1)
		na = start(self.ruimte, "planning.ifc", b"nog eens", basis_versie=1, basis_bestand=f1, bij_conflict="kopie")
		for r in (api.upload_klaar(**voor), api.upload_klaar(**na)):
			self.assertTrue(r["conflict"])
			self.assertIn("(conflict", r["naam"])
			self.assertNotIn(r["bestand"], (f1, f2))
			self.assertEqual(r["versie"], 1)
		self.assertEqual(self.volgnummers(f2), [1])
		self.assertEqual(self.inhoud(f2), b"van piet")
		self.assertEqual(len(self.actief("planning.ifc")), 3)

	def test_basis_bestand_ander_bestand_in_upload_klein(self):
		f1, f2 = self.vervangen_door_piet("planning.txt")
		bestanden = [
			(item("planning.txt", b"w", basis_versie=1, basis_bestand=f1, bij_conflict="weiger"), b"w"),
			(item("planning.txt", b"k", basis_versie=1, basis_bestand=f1), b"k"),
			(item("planning.txt", b"k2", basis_versie=1, basis_bestand=f1, bij_conflict="kopie"), b"k2"),
		]
		r = api.upload_klein_bytes(self.ruimte, [i for i, _ in bestanden], {f"f{n}": d for n, (_, d) in enumerate(bestanden)})[
			"resultaten"
		]
		self.assertEqual(
			(r[0]["ok"], r[0]["foutcode"], r[0]["huidige_versie"], r[0]["huidig_bestand"]), (False, "verouderde_basis", 1, f2)
		)
		for kopie in r[1:]:
			self.assertTrue(kopie["ok"], kopie)
			self.assertTrue(kopie["conflict"])
			self.assertNotIn(kopie["bestand"], (f1, f2))
		self.assertEqual(self.volgnummers(f2), [1])
		self.assertEqual(self.inhoud(f2), b"van piet")
		self.assertNotIn("huidig_bestand", frappe.local.response)

	def test_basis_bestand_weg_en_niets_op_de_plek(self):
		f1 = upload(self.ruimte, "planning.ifc", b"van anna")["bestand"]
		met_weiger = start(self.ruimte, "planning.ifc", b"oud", basis_versie=1, basis_bestand=f1, bij_conflict="weiger")
		met_kopie = start(self.ruimte, "planning.ifc", b"oud", basis_versie=1, basis_bestand=f1)
		frappe.set_user(self.piet)
		api.verwijder(bestand=f1)
		frappe.set_user(self.anna)
		# Met weiger: verouderde_basis, en er staat geen bestand (huidig_bestand null). Bij het begin, bij het afronden
		# en in upload_klein.
		code = foutcode(
			self, api.VerouderdeBasis, start, self.ruimte, "planning.ifc", b"oud", basis_versie=1, basis_bestand=f1, bij_conflict="weiger"
		)
		self.assertEqual(code, "verouderde_basis")
		self.assertEqual(self.basis_in_antwoord(), (None, None))
		self.assertEqual(foutcode(self, api.VerouderdeBasis, api.upload_klaar, **met_weiger), "verouderde_basis")
		self.assertEqual(self.basis_in_antwoord(), (None, None))
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("planning.ifc", b"oud", basis_versie=1, basis_bestand=f1, bij_conflict="weiger")], {"f0": b"oud"}
		)["resultaten"]
		self.assertEqual((k["ok"], k["foutcode"], k["huidige_versie"], k["huidig_bestand"]), (False, "verouderde_basis", None, None))
		self.assertEqual(self.actief("planning.ifc"), [])
		# Met kopie: een nieuw bestand, zoals zonder basis_bestand.
		r = api.upload_klaar(**met_kopie)
		self.assertEqual((r["naam"], r["versie"], r["conflict"]), ("planning.ifc", 1, False))
		self.assertNotEqual(r["bestand"], f1)
		g1 = upload(self.ruimte, "rooster.txt", b"r1")["bestand"]
		api.verwijder(bestand=g1)
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("rooster.txt", b"r2", basis_versie=1, basis_bestand=g1)], {"f0": b"r2"}
		)["resultaten"]
		self.assertEqual((k["ok"], k["naam"], k["versie"], k["conflict"]), (True, "rooster.txt", 1, False))
		self.assertNotEqual(k["bestand"], g1)

	def test_basis_bestand_gelijk_aan_het_bestand_op_de_plek(self):
		bestand = upload(self.ruimte, "plan.ifc", b"een")["bestand"]
		r = api.upload_klaar(**start(self.ruimte, "plan.ifc", b"twee", basis_versie=1, basis_bestand=bestand, bij_conflict="weiger"))
		self.assertEqual((r["bestand"], r["versie"], r["conflict"]), (bestand, 2, False))
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("plan.ifc", b"drie", basis_versie=2, basis_bestand=bestand, bij_conflict="weiger")], {"f0": b"drie"}
		)["resultaten"]
		self.assertEqual((k["ok"], k["bestand"], k["versie"]), (True, bestand, 3))
		# Hetzelfde bestand met een verouderde versie: zoals voorheen, nu met huidig_bestand erbij.
		code = foutcode(
			self, api.VerouderdeBasis, start, self.ruimte, "plan.ifc", b"x", basis_versie=1, basis_bestand=bestand, bij_conflict="weiger"
		)
		self.assertEqual(code, "verouderde_basis")
		self.assertEqual(self.basis_in_antwoord(), (3, bestand))

	def test_samengevoegd_uit_geldt_niet_voor_een_ander_bestand(self):
		"""Anna voegde versie 1 en 2 van F1 samen; intussen staat er F2 (alleen versie 1). Met `basis_bestand` wordt
		het een conflictkopie zonder samenvoeging, geen `ongeldige_samenvoeging` op versies van F2."""
		f1 = upload(self.ruimte, "planning.ifc", b"een")["bestand"]
		upload(self.ruimte, "planning.ifc", b"twee", basis_versie=1)
		f2 = self.piet_vervangt(f1)
		r = api.upload_klaar(
			**start(
				self.ruimte, "planning.ifc", b"een+twee", basis_versie=2, basis_bestand=f1, samengevoegd_uit='{"basis": 1, "hun": 2}'
			)
		)
		self.assertTrue(r["conflict"])
		self.assertNotEqual(r["bestand"], f2)
		self.assertEqual([v.samengevoegd_uit for v in api.versies(bestand=r["bestand"])], [None])
		self.assertEqual(self.volgnummers(f2), [1])


	# --- de basis is de versieregel (versie_id), niet het nummer -----------------------------------------

	def regel(self, bestand: str, versie: int) -> str:
		return frappe.db.get_value("Bestandsversie", {"bestand": bestand, "volgnummer": versie}, "name")

	def huidig(self, bestand: str) -> tuple:
		return tuple(frappe.db.get_value("Opslagbestand", bestand, ["huidige_versie", "huidige_versie_id"]))

	def huidig_id(self, bestand: str) -> str | None:
		return frappe.db.get_value("Opslagbestand", bestand, "huidige_versie_id")

	def lees(self, bestand: str, versie_id: str) -> bytes:
		return requests.get(api.download(bestand=bestand, versie_id=versie_id)["url"], timeout=30).content

	def planning_met_twee_versies(self) -> str:
		"""Anna's planning.ifc (F1): versie 1, en versie 2 met inhoud A."""
		f1 = upload(self.ruimte, "planning.ifc", b"een")["bestand"]
		upload(self.ruimte, "planning.ifc", b"inhoud A", basis_versie=1)
		return f1

	def piet_verplaatst_over_een_ander(self, f1: str) -> str:
		"""Piet verplaatst F1 met overschrijven over ander.ifc: de huidige versie (A) gaat naar ander.ifc, F1 staat als
		versie 1 in de prullenbak. Geeft ander.ifc."""
		frappe.set_user(self.piet)
		try:
			ander = upload(self.ruimte, "ander.ifc", b"ander")["bestand"]
			api.verplaats(bestand=f1, naam="ander.ifc", overschrijf=1)
			return ander
		finally:
			frappe.set_user(self.anna)

	def voor_de_invoering(self) -> None:
		"""De stand van vóór de invoering: nog geen `huidige_versie_id` (alleen in de database)."""
		frappe.db.sql("update `tabOpslagbestand` set huidige_versie_id = null where ruimte = %s", self.ruimte)

	def stand(self) -> tuple:
		"""Alles wat de patch kan veranderen, voor de controle dat een tweede keer niets verandert."""
		return (
			frappe.db.sql(
				"""select name, huidige_versie, huidige_versie_id, grootte from `tabOpslagbestand` where ruimte = %s
				order by name""",
				self.ruimte,
			),
			frappe.db.sql("select name, bestand, volgnummer from `tabBestandsversie` where ruimte = %s order by name", self.ruimte),
		)

	def quotum(self) -> int:
		return frappe.db.sql("select coalesce(sum(grootte), 0) from `tabBestandsversie` where ruimte = %s", self.ruimte)[0][0]

	def test_versie_id_in_de_antwoorden(self):
		"""`versie_id` (het id van de versieregel) in lijsten, `wijzigingen`, `versies`, `info`, `download` en de
		antwoorden van een upload; `huidige_versie_id` op het bestand is dezelfde regel."""
		r = upload(self.ruimte, "plan.ifc", b"een")
		bestand, regel = r["bestand"], r["versie_id"]
		self.assertEqual(self.huidig(bestand), (1, regel))
		self.assertEqual(regel, self.regel(bestand, 1))
		map_id = api.maak_map(ruimte=self.ruimte, naam="Leeg")["map"]
		self.assertEqual([b.versie_id for b in api.bladeren(ruimte=self.ruimte)["bestanden"]], [regel])
		self.assertEqual([b.versie_id for b in api.boom(ruimte=self.ruimte)["bestanden"]], [regel])
		self.assertEqual([v.versie_id for v in api.versies(bestand=bestand)], [regel])
		self.assertEqual(api.info(bestand=bestand)["versie_id"], regel)
		self.assertEqual(api.download(bestand=bestand)["versie_id"], regel)
		nummer_wijzigingen()
		wijzigingen = api.wijzigingen(ruimte=self.ruimte)["wijzigingen"]
		self.assertEqual({w["versie_id"] for w in wijzigingen if w["doel"] == bestand}, {regel})
		self.assertEqual({w["versie_id"] for w in wijzigingen if w["doel"] == map_id}, {None})
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("plan.ifc", b"twee", basis_versie_id=regel, bij_conflict="weiger")], {"f0": b"twee"}
		)["resultaten"]
		self.assertEqual((k["ok"], k["bestand"], k["versie"], k["versie_id"]), (True, bestand, 2, self.regel(bestand, 2)))
		self.assertEqual(self.huidig(bestand), (2, k["versie_id"]))
		(d,) = api.download_klein([bestand])["bestanden"]
		self.assertEqual((d["versie"], d["versie_id"]), (2, k["versie_id"]))
		(d,) = api.download_klein([{"bestand": bestand, "versie_id": regel}])["bestanden"]
		self.assertEqual((d["versie"], d["versie_id"], d["inhoud"]), (1, regel, "ZWVu"))
		self.assertEqual(self.lees(bestand, regel), b"een")

	def test_versie_id_ander_bestand_op_de_plek_geeft_onbekende_basis(self):
		"""F1/F2: Anna werkt vanuit F1 versie 1; Piet verwijdert F1 en maakt onder dezelfde naam F2. Met
		`basis_versie_id` is dat `onbekende_basis` (de regel hoort niet bij F2), niet `verouderde_basis`."""
		f1 = upload(self.ruimte, "planning.ifc", b"van anna")["bestand"]
		v1 = self.huidig_id(f1)
		met_weiger = start(
			self.ruimte, "planning.ifc", b"oud van anna", basis_bestand=f1, basis_versie_id=v1, bij_conflict="weiger"
		)
		sleutel = sleutel_van(met_weiger)
		f2 = self.piet_vervangt(f1)
		w2 = self.huidig_id(f2)
		# Onder het slot, bij het afronden.
		self.assertEqual(foutcode(self, api.OnbekendeBasis, api.upload_klaar, **met_weiger), "onbekende_basis")
		self.assertEqual(self.basis_id_in_antwoord(), (1, w2, f2))
		self.assertFalse(self.bestaat_in_s3(sleutel), "het object van de geweigerde upload staat nog in de opslag")
		# Bij het begin, met en zonder basis_bestand: de regel hoort hoe dan ook niet bij F2.
		for extra in ({"basis_bestand": f1}, {}):
			code = foutcode(
				self, api.OnbekendeBasis, start, self.ruimte, "planning.ifc", b"oud", basis_versie_id=v1, bij_conflict="weiger", **extra
			)
			self.assertEqual(code, "onbekende_basis")
			self.assertEqual(self.basis_id_in_antwoord(), (1, w2, f2))
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("planning.ifc", b"oud", basis_bestand=f1, basis_versie_id=v1, bij_conflict="weiger")], {"f0": b"oud"}
		)["resultaten"]
		self.assertEqual(
			(k["ok"], k["foutcode"], k["huidige_versie"], k["huidige_versie_id"], k["huidig_bestand"]),
			(False, "onbekende_basis", 1, w2, f2),
		)
		# Met kopie (standaard): een conflictkopie, ook in upload_klein; F2 blijft staan.
		r = api.upload_klaar(**start(self.ruimte, "planning.ifc", b"oud van anna", basis_bestand=f1, basis_versie_id=v1))
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("planning.ifc", b"oud2", basis_versie_id=v1)], {"f0": b"oud2"}
		)["resultaten"]
		for kopie in (r, k):
			self.assertTrue(kopie["conflict"], kopie)
			self.assertNotIn(kopie["bestand"], (f1, f2))
		self.assertEqual(self.volgnummers(f2), [1])
		self.assertEqual(self.inhoud(f2), b"van piet")
		# Staat er niets meer op de plek: met weiger ook onbekende_basis (alles null), met kopie een nieuw bestand.
		api.verwijder(bestand=f2)
		code = foutcode(
			self, api.OnbekendeBasis, start, self.ruimte, "planning.ifc", b"x", basis_bestand=f2, basis_versie_id=w2, bij_conflict="weiger"
		)
		self.assertEqual(code, "onbekende_basis")
		self.assertEqual(self.basis_id_in_antwoord(), (None, None, None))
		r = api.upload_klaar(**start(self.ruimte, "planning.ifc", b"x", basis_bestand=f2, basis_versie_id=w2))
		self.assertEqual((r["naam"], r["versie"], r["conflict"]), ("planning.ifc", 1, False))
		self.assertNotIn(r["bestand"], (f1, f2))

	def test_versie_id_na_verplaatsen_herstellen_en_opnieuw_uploaden(self):
		"""Ronde 11: Anna werkt offline vanuit (F1, 2) = A. Piet verplaatst F1 met overschrijven over ander.ifc (A gaat
		mee), herstelt F1 en uploadt B: dat is weer F1 versie 2. Het nummer kwam terug, het id niet."""
		f1 = self.planning_met_twee_versies()
		a, v1 = self.huidig_id(f1), self.regel(f1, 1)
		met_weiger = start(
			self.ruimte, "planning.ifc", b"oud werk van anna", basis_bestand=f1, basis_versie=2, basis_versie_id=a, bij_conflict="weiger"
		)
		ander = self.piet_verplaatst_over_een_ander(f1)
		# Verplaatsen zet de huidige regel van beide: A is de huidige van ander.ifc (versie 2), F1 zakt naar versie 1.
		self.assertEqual(self.huidig(ander), (2, a))
		self.assertEqual(self.huidig(f1), (1, v1))
		frappe.set_user(self.piet)
		api.herstel(bestand=f1)
		b = upload(self.ruimte, "planning.ifc", b"inhoud B", basis_versie=1)
		frappe.set_user(self.anna)
		self.assertEqual((b["bestand"], b["versie"]), (f1, 2))
		self.assertEqual(self.huidig(f1), (2, b["versie_id"]))
		self.assertNotEqual(b["versie_id"], a)
		# Anna's basis (F1, A): onbekende_basis bij het afronden, bij het begin en in upload_klein.
		self.assertEqual(foutcode(self, api.OnbekendeBasis, api.upload_klaar, **met_weiger), "onbekende_basis")
		self.assertEqual(self.basis_id_in_antwoord(), (2, b["versie_id"], f1))
		code = foutcode(
			self, api.OnbekendeBasis, start, self.ruimte, "planning.ifc", b"oud", basis_bestand=f1, basis_versie_id=a, bij_conflict="weiger"
		)
		self.assertEqual(code, "onbekende_basis")
		self.assertEqual(self.basis_id_in_antwoord(), (2, b["versie_id"], f1))
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("planning.ifc", b"oud", basis_bestand=f1, basis_versie_id=a, bij_conflict="weiger")], {"f0": b"oud"}
		)["resultaten"]
		self.assertEqual((k["ok"], k["foutcode"], k["huidige_versie_id"]), (False, "onbekende_basis", b["versie_id"]))
		# Lezen op versie_id: A hoort niet meer bij F1 (onbekende_basis, ook in download_klein); bij ander.ifc wel.
		self.assertEqual(foutcode(self, api.OnbekendeBasis, api.download, bestand=f1, versie_id=a), "onbekende_basis")
		(d,) = api.download_klein([{"bestand": f1, "versie_id": a}])["bestanden"]
		self.assertEqual((d["ok"], d["foutcode"]), (False, "onbekende_basis"))
		self.assertEqual(self.lees(ander, a), b"inhoud A")
		# Met kopie: een conflictkopie, en B blijft staan.
		r = api.upload_klaar(**start(self.ruimte, "planning.ifc", b"oud werk van anna", basis_bestand=f1, basis_versie_id=a))
		self.assertTrue(r["conflict"])
		self.assertNotEqual(r["bestand"], f1)
		self.assertEqual(self.volgnummers(f1), [1, 2])
		self.assertEqual(self.inhoud(f1), b"inhoud B")

	def test_twee_regels_op_hetzelfde_nummer_na_de_patch(self):
		"""Twee verplaatsingen tegelijk naar F0 (vóór het slot op het doel) lieten A en B allebei als (F0, 2) achter;
		Anna haalde A op. De patch maakt B (de laatst gewijzigde) de huidige en geeft A een vrij nummer (3). Anna's basis
		A is dan verouderd, niet onbekend: A hoort bij F0, en lezen op versie_id geeft A, zodat de opslaan-lus kan
		samenvoegen."""
		f0 = upload(self.ruimte, "rooster.ifc", b"een")["bestand"]
		bron_a = upload(self.ruimte, "a.tmp", b"inhoud A")["bestand"]
		bron_b = upload(self.ruimte, "b.tmp", b"inhoud B")["bestand"]
		regel_a, regel_b = self.huidig_id(bron_a), self.huidig_id(bron_b)
		# Zoals de race het achterliet, direct in de database: beide regels als versie 2 van F0, de bronnen weg.
		for regel, moment in ((regel_a, "2026-10-30 10:00:00"), (regel_b, "2026-10-30 10:00:01")):
			frappe.db.sql(
				"update `tabBestandsversie` set bestand = %s, volgnummer = 2, modified = %s where name = %s", (f0, moment, regel)
			)
		frappe.db.sql("update `tabOpslagbestand` set huidige_versie = 2 where name = %s", f0)
		frappe.db.delete("Opslagbestand", {"name": ("in", [bron_a, bron_b])})
		self.voor_de_invoering()
		quotum = self.quotum()
		huidige_versie_id_invullen.execute()
		self.assertEqual(self.huidig(f0), (2, regel_b))
		self.assertEqual(
			[frappe.db.get_value("Bestandsversie", r, "volgnummer") for r in (regel_b, regel_a)], [2, 3]
		)
		self.assertEqual(self.volgnummers(f0), [1, 2, 3])
		self.assertEqual(self.quotum(), quotum, "het quotum verandert niet: dezelfde regels, geen kopie")
		self.assertEqual((self.inhoud(f0), self.inhoud(f0, 3)), (b"inhoud B", b"inhoud A"))
		# Een tweede keer de patch: niets verandert.
		stand = self.stand()
		huidige_versie_id_invullen.execute()
		self.assertEqual(self.stand(), stand)
		# Anna's basis A: verouderde_basis, met de huidige regel B.
		code = foutcode(
			self, api.VerouderdeBasis, start, self.ruimte, "rooster.ifc", b"A+", basis_bestand=f0, basis_versie_id=regel_a, bij_conflict="weiger"
		)
		self.assertEqual(code, "verouderde_basis")
		self.assertEqual(self.basis_id_in_antwoord(), (2, regel_b, f0))
		# Lezen op versie_id geeft A; de lus voegt A, B en haar werk samen en schrijft met basis B: versie 4.
		self.assertEqual(self.lees(f0, regel_a), b"inhoud A")
		r = api.upload_klaar(
			**start(self.ruimte, "rooster.ifc", b"A+B", basis_bestand=f0, basis_versie_id=regel_b, bij_conflict="weiger")
		)
		self.assertEqual((r["bestand"], r["versie"], r["conflict"]), (f0, 4, False))
		self.assertEqual(self.volgnummers(f0), [1, 2, 3, 4])
		self.assertEqual(self.huidig(f0), (4, r["versie_id"]))

	def test_regel_die_van_f_naar_h_schoof(self):
		"""Ronde 13: twee verplaatsingen van dezelfde bron (vóór het slot op de bron) hingen regel R eerst aan F en
		daarna aan H, zonder `verwijderd` voor F; F wees nog naar R. Een basis R op F is onbekend: R hoort bij H."""
		f = upload(self.ruimte, "f.ifc", b"f een")["bestand"]
		h = upload(self.ruimte, "h.ifc", b"h een")["bestand"]
		bron = upload(self.ruimte, "s.tmp", b"inhoud R")["bestand"]
		r, f1 = self.huidig_id(bron), self.huidig_id(f)
		# Zoals de race het achterliet: R hangt als versie 2 aan H; F en H staan allebei op versie 2 met R als huidige.
		frappe.db.sql("update `tabBestandsversie` set bestand = %s, volgnummer = 2 where name = %s", (h, r))
		frappe.db.sql(
			"update `tabOpslagbestand` set huidige_versie = 2, huidige_versie_id = %s where name in (%s, %s)", (r, f, h)
		)
		frappe.db.delete("Opslagbestand", {"name": bron})
		# Ook zolang F nog naar R wijst: een basis R op F is onbekend, bij schrijven en bij lezen.
		code = foutcode(
			self, api.OnbekendeBasis, start, self.ruimte, "f.ifc", b"x", basis_bestand=f, basis_versie_id=r, bij_conflict="weiger"
		)
		self.assertEqual(code, "onbekende_basis")
		self.basis_id_in_antwoord()
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("f.ifc", b"x", basis_versie_id=r, bij_conflict="weiger")], {"f0": b"x"}
		)["resultaten"]
		self.assertEqual((k["ok"], k["foutcode"]), (False, "onbekende_basis"))
		self.assertEqual(foutcode(self, api.OnbekendeBasis, api.download, bestand=f, versie_id=r), "onbekende_basis")
		# De patch: F heeft geen regel op versie 2, dus de hoogste bestaande (1) wordt de huidige. H houdt R.
		self.voor_de_invoering()
		huidige_versie_id_invullen.execute()
		self.assertEqual(self.huidig(f), (1, f1))
		self.assertEqual(frappe.db.get_value("Opslagbestand", f, "grootte"), len(b"f een"))
		self.assertEqual(self.huidig(h), (2, r))
		self.assertEqual(self.inhoud(f), b"f een")
		code = foutcode(
			self, api.OnbekendeBasis, start, self.ruimte, "f.ifc", b"x", basis_bestand=f, basis_versie_id=r, bij_conflict="weiger"
		)
		self.assertEqual(code, "onbekende_basis")
		self.assertEqual(self.basis_id_in_antwoord(), (1, f1, f))
		# Op H is R de huidige: gewoon een nieuwe versie.
		nieuw = api.upload_klaar(
			**start(self.ruimte, "h.ifc", b"h drie", basis_bestand=h, basis_versie_id=r, bij_conflict="weiger")
		)
		self.assertEqual((nieuw["bestand"], nieuw["versie"], nieuw["conflict"]), (h, 3, False))

	def test_verse_ophaal_na_de_patch_en_opslaan(self):
		"""Wie na de invoering ophaalt, krijgt de `versie_id` van de huidige versie, en opslaan daarmee is gewoon een
		nieuwe versie. De patch hernummert niets als er niets mis is; een bestand zonder versies houdt geen id."""
		g = upload(self.ruimte, "schema.ifc", b"een")["bestand"]
		upload(self.ruimte, "schema.ifc", b"twee", basis_versie=1)
		leeg = (
			frappe.get_doc({"doctype": "Opslagbestand", "ruimte": self.ruimte, "naam": "leeg.ifc", "huidige_versie": 0})
			.insert(ignore_permissions=True)
			.name
		)
		self.voor_de_invoering()
		versies = self.stand()[1]
		huidige_versie_id_invullen.execute()
		self.assertEqual(self.stand()[1], versies, "de patch hernummert niets als er niets mis is")
		self.assertEqual(self.huidig(g), (2, self.regel(g, 2)))
		self.assertEqual(self.huidig(leeg), (0, None))
		opgehaald = api.download(bestand=g)
		self.assertEqual((opgehaald["versie"], opgehaald["versie_id"]), (2, self.regel(g, 2)))
		r = api.upload_klaar(
			**start(self.ruimte, "schema.ifc", b"drie", basis_bestand=g, basis_versie_id=opgehaald["versie_id"], bij_conflict="weiger")
		)
		self.assertEqual((r["bestand"], r["versie"], r["conflict"]), (g, 3, False))
		self.assertEqual(self.huidig(g), (3, r["versie_id"]))
		# Een bestand zonder versies: elke basis is onbekend.
		code = foutcode(
			self, api.OnbekendeBasis, start, self.ruimte, "leeg.ifc", b"x", basis_bestand=leeg, basis_versie_id=r["versie_id"], bij_conflict="weiger"
		)
		self.assertEqual(code, "onbekende_basis")
		self.assertEqual(self.basis_id_in_antwoord(), (0, None, leeg))

	def test_oude_client_zonder_basis_versie_id_houdt_het_gedrag_op_nummers(self):
		"""Zonder `basis_versie_id` vergelijkt de server op `basis_versie`, zoals voorheen, met wat `basis_bestand` al
		deed. Ook het risico van een teruggekomen nummer blijft voor zo'n client; de desktop-sync van stap 5 stuurt
		altijd `basis_versie_id`."""
		f1 = self.planning_met_twee_versies()
		self.piet_verplaatst_over_een_ander(f1)
		frappe.set_user(self.piet)
		api.herstel(bestand=f1)
		b = upload(self.ruimte, "planning.ifc", b"inhoud B", basis_versie=1)
		frappe.set_user(self.anna)
		self.assertEqual(b["versie"], 2)
		# Anna's oude client met basis (F1, 2): op nummers is dat de huidige versie, dus een nieuwe versie.
		r = api.upload_klaar(
			**start(self.ruimte, "planning.ifc", b"oud werk", basis_versie=2, basis_bestand=f1, bij_conflict="weiger")
		)
		self.assertEqual((r["bestand"], r["versie"], r["conflict"]), (f1, 3, False))
		# Een verouderd nummer: verouderde_basis, nu ook met huidige_versie_id.
		code = foutcode(
			self, api.VerouderdeBasis, start, self.ruimte, "planning.ifc", b"x", basis_versie=1, basis_bestand=f1, bij_conflict="weiger"
		)
		self.assertEqual(code, "verouderde_basis")
		self.assertEqual(self.basis_id_in_antwoord(), (3, r["versie_id"], f1))

	def test_dossier_zet_de_revisie_op_de_huidige_versieregel(self):
		"""Revisie en status gaan naar de regel van `huidige_versie_id`, niet naar elke regel met het huidige nummer
		(vóór de invoering konden dat er twee zijn)."""
		from openaec_storage import dossier

		f0 = upload(self.ruimte, "tekening.pdf", b"een")["bestand"]
		los = upload(self.ruimte, "los.tmp", b"twee")["bestand"]
		b, a = self.huidig_id(f0), self.huidig_id(los)
		frappe.db.sql("update `tabBestandsversie` set bestand = %s, volgnummer = 1 where name = %s", (f0, a))
		frappe.db.delete("Opslagbestand", {"name": los})
		dossier.zet_eigenschappen(bestanden=[f0], waarden={"revisie": "B"})
		self.assertEqual(
			[frappe.db.get_value("Bestandsversie", r, "revisie") for r in (b, a)], ["B", None]
		)

	# --- versieregels zijn niet te wijzigen via de algemene routes van Frappe ----------------------------

	def versieregel(self) -> tuple:
		"""Een bestand van Anna met twee versies: (bestand, de regel van versie 1, zijn s3_sleutel). Versie 1 is niet de
		huidige: daar wijst geen link naar, dus alleen de haak in `rechten.heeft_recht` houdt wissen tegen."""
		bestand = upload(self.ruimte, "plan.ifc", b"een")["bestand"]
		upload(self.ruimte, "plan.ifc", b"twee", basis_versie=1)
		regel = self.regel(bestand, 1)
		# Lezen mag wel, en het bestand zelf mag Anna (schrijver) ook wijzigen.
		self.assertTrue(frappe.has_permission("Bestandsversie", "read", regel))
		self.assertTrue(frappe.has_permission("Opslagbestand", "write", bestand))
		return bestand, regel, frappe.db.get_value("Bestandsversie", regel, "s3_sleutel")

	def onveranderd(self, bestand: str, regel: str, sleutel: str) -> None:
		self.assertEqual(
			frappe.db.get_value("Bestandsversie", regel, ["bestand", "volgnummer", "s3_sleutel"]), (bestand, 1, sleutel)
		)
		self.assertEqual(self.volgnummers(bestand), [1, 2])

	def nieuwe_regel(self, bestand: str) -> dict:
		return {"doctype": "Bestandsversie", "bestand": bestand, "ruimte": self.ruimte, "volgnummer": 9, "s3_sleutel": "ander"}

	def test_api_resource_wijzigt_maakt_en_wist_geen_versieregel(self):
		"""`/api/resource/Bestandsversie` (API v1): PUT, POST en DELETE."""
		from frappe.api import v1

		bestand, regel, sleutel = self.versieregel()
		for naam, functie, args, data in (
			("PUT", v1.update_doc, ("Bestandsversie", regel), {"s3_sleutel": "ander"}),
			("POST", v1.create_doc, ("Bestandsversie",), self.nieuwe_regel(bestand)),
			("DELETE", v1.delete_doc, ("Bestandsversie", regel), {}),
		):
			frappe.local.form_dict = frappe._dict(data=json.dumps(data))
			with self.subTest(naam), self.assertRaises(frappe.PermissionError):
				functie(*args)
		self.onveranderd(bestand, regel, sleutel)

	def test_frappe_client_set_value_wijzigt_geen_versieregel(self):
		import frappe.client

		bestand, regel, sleutel = self.versieregel()
		with self.assertRaises(frappe.PermissionError):
			frappe.client.set_value("Bestandsversie", regel, "s3_sleutel", "ander")
		with self.assertRaises(frappe.PermissionError):
			frappe.client.set_value("Bestandsversie", regel, {"bestand": "ander", "volgnummer": 7})
		self.onveranderd(bestand, regel, sleutel)

	def test_frappe_client_save_insert_en_delete_op_een_versieregel(self):
		import frappe.client

		bestand, regel, sleutel = self.versieregel()
		doc = frappe.get_doc("Bestandsversie", regel).as_dict()
		doc["s3_sleutel"] = "ander"
		for naam, functie, args in (
			("save", frappe.client.save, (json.dumps(doc, default=str),)),
			("insert", frappe.client.insert, (self.nieuwe_regel(bestand),)),
			("delete", frappe.client.delete, ("Bestandsversie", regel)),
		):
			with self.subTest(naam), self.assertRaises(frappe.PermissionError):
				functie(*args)
		self.onveranderd(bestand, regel, sleutel)

	def test_api_v2_wijzigt_maakt_en_wist_geen_versieregel(self):
		"""`/api/v2/document/Bestandsversie`: PATCH/PUT, POST en DELETE."""
		from frappe.api import v2

		bestand, regel, sleutel = self.versieregel()
		for naam, functie, args, data in (
			("PATCH", v2.update_doc, ("Bestandsversie", regel), {"s3_sleutel": "ander"}),
			("POST", v2.create_doc, ("Bestandsversie",), self.nieuwe_regel(bestand)),
			("DELETE", v2.delete_doc, ("Bestandsversie", regel), {}),
		):
			frappe.local.form_dict = frappe._dict(data)
			with self.subTest(naam), self.assertRaises(frappe.PermissionError):
				functie(*args)
		self.onveranderd(bestand, regel, sleutel)

	def test_huidige_versie_id_verandert_niet_via_de_algemene_routes(self):
		"""Anna mag het bestand wijzigen, maar niet welke versie de huidige is: anders kon een oude basis weer de huidige
		worden."""
		import frappe.client

		bestand = self.drie_versies()
		oud = self.regel(bestand, 1)
		frappe.client.set_value("Opslagbestand", bestand, {"huidige_versie": 1, "huidige_versie_id": oud})
		self.assertEqual(self.huidig(bestand), (3, self.regel(bestand, 3)))
		code = foutcode(
			self, api.VerouderdeBasis, start, self.ruimte, "plan.ifc", b"x", basis_bestand=bestand, basis_versie_id=oud, bij_conflict="weiger"
		)
		self.assertEqual(code, "verouderde_basis")
		self.basis_id_in_antwoord()


# --- gelijktijdig: twee echte databaseverbindingen ------------------------------------------------------


class Verzoek(threading.Thread):
	"""Eén verzoek in een eigen thread met een eigen Frappe-context en databaseverbinding, zoals op een tweede werker.
	Aan het eind een commit, zoals Frappe na een verzoek doet; bij een fout een rollback, met de foutcode en
	`huidige_versie` uit het antwoord."""

	def __init__(self, naam: str, test, gebruiker: str, functie):
		super().__init__(name=naam, daemon=True)
		self.site, self.sites_path, self.conf = test.site, test.sites_path, test.conf
		self.gebruiker, self.functie = gebruiker, functie
		self.resultaat = self.fout = self.foutcode = self.huidige_versie = self.verbinding = None
		self.klaar = threading.Event()

	def run(self):
		try:
			frappe.init(site=self.site, sites_path=self.sites_path)
			frappe.connect()
			for sleutel, waarde in self.conf.items():
				if waarde is None:
					frappe.local.conf.pop(sleutel, None)
				else:
					frappe.local.conf[sleutel] = waarde
			# Dit verzoek legt vast zoals buiten de tests: in een eigen transactie (zie api._eigen_transactie).
			frappe.flags.oa_eigen_transactie = True
			self.verbinding = frappe.db.sql("select connection_id()")[0][0]
			frappe.set_user(self.gebruiker)
			try:
				self.resultaat = self.functie()
				frappe.db.commit()
			except Exception as fout:
				self.fout = fout
				self.foutcode = frappe.local.response.get("foutcode")
				self.huidige_versie = frappe.local.response.get("huidige_versie")
				frappe.db.rollback()
		except Exception as fout:
			self.fout = self.fout or fout
		finally:
			self.klaar.set()
			try:
				frappe.destroy()
			except Exception:
				pass


class Wissel:
	"""Vaste volgorde: A legt een versie vast en wacht vóór zijn commit; B komt bij het vastleggen; dan commit A; dan
	gaat B verder. Vervangt `api._registreer` in beide threads."""

	def __init__(self, origineel=None):
		self.origineel = origineel or api._registreer
		self.a_vastgelegd = threading.Event()
		self.a_door = threading.Event()
		self.b_bij_vastleggen = threading.Event()

	def registreer(self, *args, **kwargs):
		naam = threading.current_thread().name
		if naam == "B":
			self.b_bij_vastleggen.set()
		uit = self.origineel(*args, **kwargs)
		if naam == "A":
			self.a_vastgelegd.set()
			if not self.a_door.wait(WACHT):
				raise RuntimeError("A kreeg geen sein om door te gaan")
		return uit


def wacht_tot(sein: threading.Event, verzoek: Verzoek) -> bool:
	"""Wacht op `sein`, of tot het verzoek klaar is (dan komt het sein niet meer)."""
	einde = time.monotonic() + WACHT
	while time.monotonic() < einde:
		if sein.is_set():
			return True
		if verzoek.klaar.is_set():
			return sein.is_set()
		time.sleep(0.02)
	return False


class TestGelijktijdig(Hulp, IntegrationTestCase):
	"""Twee uploads tegelijk: altijd één nieuwe versie plus een conflictkopie (of `verouderde_basis` met
	`weiger`), nooit een fout, nooit twee versies met hetzelfde volgnummer, nooit een stil overschreven versie en nooit
	twee actieve bestanden met dezelfde naam."""

	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagvergrendeling")
		self.s3 = NagebootsteS3().start()
		self.anna = gebruiker("anna")
		self.piet = gebruiker("piet")
		self.ruimte = ruimte("Project Samen", {self.anna: "schrijver", self.piet: "schrijver"})
		frappe.set_user(self.anna)
		self.map = api.maak_map(ruimte=self.ruimte, naam="Modellen")["map"]
		self.site, self.sites_path = frappe.local.site, frappe.local.sites_path
		self.conf = {k: frappe.local.conf.get(k) for k in OPSLAGINSTELLINGEN}
		# De threads hebben een eigen verbinding: wat zij moeten zien, moet echt vastliggen.
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.db.delete("Opslagvergrendeling")
		frappe.db.delete("Opslagdeling", {"ruimte": self.ruimte})
		frappe.db.delete("Notification Log", {"for_user": self.anna})
		ruim_op()
		frappe.db.commit()
		self.s3.stop()

	def wacht_op(self, verzoek: Verzoek) -> str:
		"""Waar het verzoek in de database op wacht (voor de melding bij een fout en het verslag)."""
		for query in (
			"select trx_state, trx_query from information_schema.innodb_trx where trx_mysql_thread_id = %s",
			"select state, info from information_schema.processlist where id = %s",
		):
			try:
				rij = frappe.db.sql(query, verzoek.verbinding)
			except Exception:
				continue
			if rij:
				return " | ".join(str(x) for x in rij[0])
		return "onbekend"

	def wissel(
		self, functie_a, functie_b, gebruiker_b: str | None = None, b_langs_registreer: bool = True, waar: str = "_registreer"
	):
		"""A (Anna) en B (standaard Piet) in de vaste volgorde van `Wissel`, rond `api.<waar>` (standaard
		`_registreer`). Geeft beide verzoeken en de stand van B vlak voordat A commit. Gaat B niet langs die functie (de
		uploadlink, of B wacht al ervoor op een slot), dan krijgt hij alleen de bedenktijd."""
		frappe.db.commit()
		w = Wissel(getattr(api, waar))
		a = Verzoek("A", self, self.anna, functie_a)
		b = Verzoek("B", self, gebruiker_b or self.piet, functie_b)
		stand = {"klaar": None, "wacht_op": None}
		with mock.patch.object(api, waar, w.registreer):
			try:
				a.start()
				self.assertTrue(wacht_tot(w.a_vastgelegd, a), f"A legde niets vast: {a.fout!r}")
				b.start()
				if b_langs_registreer:
					self.assertTrue(wacht_tot(w.b_bij_vastleggen, b), f"B kwam niet bij het vastleggen: {b.fout!r}")
				time.sleep(BEDENKTIJD)
				stand = {"klaar": b.klaar.is_set(), "wacht_op": self.wacht_op(b)}
			finally:
				w.a_door.set()
				a.join(WACHT)
				b.join(WACHT)
		self.assertFalse(a.is_alive() or b.is_alive(), "een verzoek hangt")
		print(f"\n  {self._testMethodName}: B vlak voor de commit van A: {stand}; A: {a.fout or a.resultaat}; B: {b.fout or b.resultaat}")
		# Een verse momentopname voor de controles hierna.
		frappe.db.rollback()
		return a, b, stand

	def basis(self, naam: str = "plan.ifc", map: str | None = None) -> str:
		bestand = upload(self.ruimte, naam, b"basis", map_naam=map)["bestand"]
		frappe.db.commit()
		return bestand

	def als(self, gebruiker: str, functie, *args, **kwargs):
		frappe.set_user(gebruiker)
		try:
			return functie(*args, **kwargs)
		finally:
			frappe.set_user(self.anna)

	def test_zelfde_basis_geeft_een_versie_en_een_conflictkopie(self):
		bestand = self.basis()
		van_anna = start(self.ruimte, "plan.ifc", b"van anna", basis_versie=1)
		van_piet = self.als(self.piet, start, self.ruimte, "plan.ifc", b"van piet", basis_versie=1)
		a, b, stand = self.wissel(lambda: api.upload_klaar(**van_anna), lambda: api.upload_klaar(**van_piet))
		self.assertIsNone(a.fout, repr(a.fout))
		self.assertIsNone(b.fout, f"B gaf een fout in plaats van een conflictkopie: {b.fout!r}")
		self.assertEqual((a.resultaat["versie"], a.resultaat["conflict"]), (2, False))
		self.assertTrue(b.resultaat["conflict"], f"B werd geen conflictkopie: {b.resultaat}")
		self.assertEqual(self.volgnummers(bestand), [1, 2])
		self.assertEqual(frappe.db.get_value("Opslagbestand", bestand, "huidige_versie"), 2)
		self.assertEqual(self.inhoud(bestand), b"van anna")
		self.assertEqual(self.inhoud(b.resultaat["bestand"]), b"van piet")
		self.assertEqual(len(self.actief("plan.ifc")), 2)
		self.assertFalse(stand["klaar"], f"B wachtte niet op het slot: {stand}")

	def test_zelfde_basis_met_weiger_geeft_verouderde_basis(self):
		bestand = self.basis()
		van_anna = start(self.ruimte, "plan.ifc", b"van anna", basis_versie=1, bij_conflict="weiger")
		van_piet = self.als(self.piet, start, self.ruimte, "plan.ifc", b"van piet", basis_versie=1, bij_conflict="weiger")
		sleutel_piet = sleutel_van(van_piet)
		a, b, stand = self.wissel(lambda: api.upload_klaar(**van_anna), lambda: api.upload_klaar(**van_piet))
		self.assertIsNone(a.fout, repr(a.fout))
		self.assertEqual((a.resultaat["versie"], a.resultaat["conflict"]), (2, False))
		self.assertIsInstance(b.fout, api.VerouderdeBasis, f"B: {b.fout!r} {b.resultaat}")
		self.assertEqual((b.foutcode, b.huidige_versie), ("verouderde_basis", 2))
		self.assertEqual(self.volgnummers(bestand), [1, 2])
		self.assertEqual(self.actief("plan.ifc"), ["plan.ifc"])
		self.assertEqual(self.inhoud(bestand), b"van anna")
		self.assertFalse(self.bestaat_in_s3(sleutel_piet))
		self.assertIsNone(frappe.cache.get_value(api.UPLOAD_SLEUTEL + van_piet["upload"], use_local_cache=False))
		self.assertFalse(stand["klaar"], f"B wachtte niet op het slot: {stand}")

	def test_twee_nieuwe_bestanden_met_dezelfde_naam(self):
		for map in (None, self.map):
			with self.subTest(plek="map" if map else "hoofdniveau"):
				van_anna = start(self.ruimte, "nieuw.ifc", b"van anna", map=map)
				van_piet = self.als(self.piet, start, self.ruimte, "nieuw.ifc", b"van piet", map=map)
				a, b, stand = self.wissel(lambda: api.upload_klaar(**van_anna), lambda: api.upload_klaar(**van_piet))
				self.assertIsNone(a.fout, repr(a.fout))
				self.assertIsNone(b.fout, f"B gaf een fout in plaats van een conflictkopie: {b.fout!r}")
				namen = self.actief("nieuw.ifc", map)
				self.assertEqual(namen.count("nieuw.ifc"), 1, f"twee actieve bestanden met dezelfde naam: {namen}")
				self.assertEqual(len(namen), 2, namen)
				self.assertEqual((a.resultaat["versie"], a.resultaat["conflict"]), (1, False))
				self.assertTrue(b.resultaat["conflict"], b.resultaat)
				self.assertEqual(self.inhoud(a.resultaat["bestand"]), b"van anna")
				self.assertEqual(self.inhoud(b.resultaat["bestand"]), b"van piet")
				self.assertFalse(stand["klaar"], f"B wachtte niet op het slot: {stand}")

	def test_upload_klein_tegelijk_met_upload_klaar(self):
		bestand = self.basis("plan.txt")
		van_anna = start(self.ruimte, "plan.txt", b"van anna", basis_versie=1)
		bestanden = [
			item("plan.txt", b"piet 1", basis_versie=1, bij_conflict="weiger"),
			item("plan.txt", b"piet 2", basis_versie=1),
			item("los.txt", b"los"),
		]
		inhoud = {"f0": b"piet 1", "f1": b"piet 2", "f2": b"los"}
		a, b, stand = self.wissel(
			lambda: api.upload_klaar(**van_anna), lambda: api.upload_klein_bytes(self.ruimte, bestanden, inhoud)
		)
		self.assertIsNone(a.fout, repr(a.fout))
		self.assertIsNone(b.fout, f"B gaf een fout: {b.fout!r}")
		r = b.resultaat["resultaten"]
		self.assertEqual(
			[(x["ok"], x.get("foutcode"), x.get("huidige_versie"), x.get("conflict")) for x in r],
			[(False, "verouderde_basis", 2, None), (True, None, None, True), (True, None, None, False)],
		)
		self.assertEqual(self.volgnummers(bestand), [1, 2])
		self.assertEqual(self.inhoud(bestand), b"van anna")
		self.assertEqual(self.inhoud(r[1]["bestand"]), b"piet 2")
		self.assertEqual(self.inhoud(r[2]["bestand"]), b"los")
		self.assertFalse(stand["klaar"], f"B wachtte niet op het slot: {stand}")

	def test_nieuwe_versie_wacht_niet_op_wie_de_map_vasthoudt(self):
		"""`_maak_map` (en zo `maak_mappen` en `kopieer`) houdt mappen vast tot het einde van zijn verzoek. Een nieuwe
		versie van een bestaand bestand vraagt dat slot niet; een nieuw bestand in die map wacht er wel op."""
		bestand = self.basis("plan.ifc", map=self.map)
		vast, los = threading.Event(), threading.Event()

		def houd_vast():
			frappe.db.sql("select name from `tabOpslagmap` where name = %s for update", self.map)
			vast.set()
			los.wait(WACHT)

		versie = start(self.ruimte, "plan.ifc", b"twee", map=self.map, basis_versie=1)
		nieuw = start(self.ruimte, "ander.ifc", b"nieuw", map=self.map)
		frappe.db.commit()
		houder = Verzoek("houder", self, self.anna, houd_vast)
		houder.start()
		c = None
		try:
			self.assertTrue(vast.wait(WACHT), repr(houder.fout))
			b = Verzoek("B", self, self.anna, lambda: api.upload_klaar(**versie))
			b.start()
			b.join(10)
			self.assertTrue(b.klaar.is_set(), f"de nieuwe versie wachtte op het slot van de map: {self.wacht_op(b)}")
			self.assertIsNone(b.fout, repr(b.fout))
			c = Verzoek("C", self, self.anna, lambda: api.upload_klaar(**nieuw))
			c.start()
			time.sleep(BEDENKTIJD)
			self.assertFalse(c.klaar.is_set(), "een nieuw bestand hoort te wachten op het slot van de map")
		finally:
			los.set()
			houder.join(WACHT)
			if c:
				c.join(WACHT)
		self.assertIsNone(c.fout, repr(c.fout))
		frappe.db.rollback()
		self.assertEqual(self.volgnummers(bestand), [1, 2])
		self.assertEqual(c.resultaat["naam"], "ander.ifc")

	def test_uploadlink_tegelijk_met_een_gewone_upload(self):
		"""De uploadlink vergrendelt dezelfde map en ziet daarna het bestand van de gewone upload: een volgnummer."""
		from openaec_storage import delen

		link = api.deel(doel_doctype="Opslagmap", doel=self.map, rechten="uploaden")
		van_anna = start(self.ruimte, "nieuw.ifc", b"van anna", map=self.map)
		frappe.set_user("Guest")
		try:
			begin = delen.upload_start(token=link["token"], naam="nieuw.ifc", grootte=len(b"van buiten"))
			antwoord = requests.put(begin["delen"][0]["url"], data=b"van buiten", timeout=30)
			antwoord.raise_for_status()
		finally:
			frappe.set_user(self.anna)
		stukken = [{"nummer": 1, "etag": antwoord.headers["ETag"]}]
		a, b, stand = self.wissel(
			lambda: api.upload_klaar(**van_anna),
			lambda: delen.upload_klaar(token=link["token"], upload=begin["upload"], delen=stukken),
			gebruiker_b="Guest",
			b_langs_registreer=False,
		)
		self.assertIsNone(a.fout, repr(a.fout))
		self.assertIsNone(b.fout, f"de uploadlink gaf een fout: {b.fout!r}")
		namen = self.actief("nieuw.ifc", self.map)
		self.assertEqual(namen, ["nieuw (2).ifc", "nieuw.ifc"], f"twee actieve bestanden met dezelfde naam? {namen}")
		self.assertEqual(b.resultaat["naam"], "nieuw (2).ifc")
		self.assertFalse(stand["klaar"], f"de uploadlink wachtte niet op het slot: {stand}")
		# De uploadlink zet ook de huidige versieregel.
		via_link = frappe.db.get_value(
			"Opslagbestand", {"ruimte": self.ruimte, "naam": "nieuw (2).ifc"}, ["name", "huidige_versie_id"], as_dict=True
		)
		self.assertEqual(via_link.huidige_versie_id, frappe.db.get_value("Bestandsversie", {"bestand": via_link.name}, "name"))

	def test_eigen_transactie_leest_wat_een_ander_net_vastlegde(self):
		"""Waarom het vastleggen een eigen transactie onder READ COMMITTED krijgt: de momentopname van een verzoek ziet
		een versie van een ander niet, en een vergrendelende lezing van die regel geeft op MariaDB 11.6+ een fout."""
		bestand = self.basis()
		van_piet = self.als(self.piet, start, self.ruimte, "plan.ifc", b"van piet", basis_versie=1)
		frappe.db.commit()
		niveau = api._isolatie()
		snapshot = cint_of(frappe.db.sql("select @@innodb_snapshot_isolation")[0][0])
		print(f"\n  isolatieniveau van een Frappe-verbinding: {niveau}; innodb_snapshot_isolation: {snapshot}")
		self.assertEqual(frappe.db.get_value("Opslagbestand", bestand, "huidige_versie"), 1)  # momentopname
		piet = Verzoek("B", self, self.piet, lambda: api.upload_klaar(**van_piet))
		piet.start()
		piet.join(WACHT)
		self.assertIsNone(piet.fout, repr(piet.fout))
		gewoon = frappe.db.sql("select huidige_versie from `tabOpslagbestand` where name = %s", bestand)[0][0]
		if niveau == "REPEATABLE READ":
			self.assertEqual(gewoon, 1, "onder REPEATABLE READ hoort de momentopname de versie van Piet niet te zien")
			if snapshot:
				with self.assertRaises(frappe.QueryDeadlockError):
					frappe.db.sql("select huidige_versie from `tabOpslagbestand` where name = %s for update", bestand)
		frappe.flags.oa_eigen_transactie = True
		try:
			with api._eigen_transactie():
				self.assertEqual(api._isolatie(), niveau, "het niveau van de sessie is meteen terug")
				vers = frappe.db.sql("select huidige_versie from `tabOpslagbestand` where name = %s", bestand)[0][0]
				vergrendeld = frappe.db.sql(
					"select huidige_versie from `tabOpslagbestand` where name = %s for update", bestand
				)[0][0]
		finally:
			frappe.flags.oa_eigen_transactie = False
		self.assertEqual((vers, vergrendeld), (2, 2))
		self.assertEqual(api._isolatie(), niveau)


	def huidig(self, bestand: str) -> tuple:
		return tuple(frappe.db.get_value("Opslagbestand", bestand, ["huidige_versie", "huidige_versie_id"]))

	def test_twee_verplaatsingen_van_dezelfde_bron_naar_twee_doelen(self):
		"""Ronde 13: twee verplaatsingen van bron S tegelijk, naar F en naar H. Zonder slot op de bron las de tweede
		dezelfde versieregel R en hing hem van F verder naar H, zonder `verwijderd` voor F. Nu wacht de tweede op het
		slot op S en verplaatst niets."""
		f = self.basis("f.ifc")
		h = self.basis("h.ifc")
		bron = self.basis("s.tmp")
		r = self.huidig(bron)[1]
		a, b, stand = self.wissel(
			onder_read_committed(lambda: api.verplaats(bestand=bron, naam="f.ifc", overschrijf=1)),
			onder_read_committed(lambda: api.verplaats(bestand=bron, naam="h.ifc", overschrijf=1)),
			b_langs_registreer=False,
			waar="_volgende_versie",
		)
		self.assertIsNone(a.fout, repr(a.fout))
		self.assertIsNotNone(b.fout, f"B verplaatste dezelfde versieregel nog eens: {b.resultaat}")
		self.assertEqual(tuple(frappe.db.get_value("Bestandsversie", r, ["bestand", "volgnummer"])), (f, 2))
		self.assertEqual(self.huidig(f), (2, r))
		self.assertEqual(self.volgnummers(h), [1])
		self.assertEqual(self.huidig(h)[0], 1)
		self.assertFalse(stand["klaar"], f"B wachtte niet op het slot op de bron: {stand}")

	def test_twee_verplaatsingen_tegelijk_naar_hetzelfde_doel(self):
		"""Twee verplaatsingen met overschrijven tegelijk naar doel D: zonder slot op D gaven ze allebei versie 2 uit.
		Nu wacht de tweede op het slot op D en krijgt versie 3."""
		doel = self.basis("d.ifc")
		s1 = self.basis("s1.tmp")
		s2 = self.basis("s2.tmp")
		r1, r2 = self.huidig(s1)[1], self.huidig(s2)[1]
		a, b, stand = self.wissel(
			onder_read_committed(lambda: api.verplaats(bestand=s1, naam="d.ifc", overschrijf=1)),
			onder_read_committed(lambda: api.verplaats(bestand=s2, naam="d.ifc", overschrijf=1)),
			b_langs_registreer=False,
			waar="_volgende_versie",
		)
		self.assertIsNone(a.fout, repr(a.fout))
		self.assertIsNone(b.fout, repr(b.fout))
		self.assertEqual((a.resultaat["versie"], b.resultaat["versie"]), (2, 3))
		self.assertEqual(self.volgnummers(doel), [1, 2, 3])
		self.assertEqual(self.huidig(doel), (3, r2))
		self.assertEqual([frappe.db.get_value("Bestandsversie", r, "volgnummer") for r in (r1, r2)], [2, 3])
		self.assertFalse(stand["klaar"], f"B wachtte niet op het slot op het doel: {stand}")

def cint_of(waarde) -> int:
	return int(waarde or 0)


def onder_read_committed(functie):
	"""`functie` in een transactie onder READ COMMITTED, zoals `_eigen_transactie`: een verbinding die op een slot
	wachtte, leest daarna de nieuwe stand in plaats van een fout 1020 te krijgen. Op deze testserver vangt
	`innodb_snapshot_isolation` een verouderde lezing ook zonder slot op; op een server zonder die instelling niet.
	Zo toont de test wat het slot zelf doet."""

	def binnen():
		api._zet_isolatie("READ COMMITTED")
		frappe.db.commit()
		return functie()

	return binnen
