"""Vergelijk-en-schrijf bij uploaden (ontwerp samenwerken, stap 4): vastleggen onder een slot, `bij_conflict` en
`samengevoegd_uit`.

`TestGelijktijdig` laat twee echte databaseverbindingen in een vaste volgorde wisselen: elk verzoek draait in een
eigen thread met een eigen Frappe-context en databaseverbinding, zoals twee verzoeken op twee werkers. Wat de andere
verbinding moet zien, is echt gecommit; die tests ruimen hun gegevens zelf op."""

import threading
import time
from unittest import mock

import frappe
import requests
from frappe.tests import IntegrationTestCase

from openaec_storage import api, s3
from openaec_storage.patches import hoogste_versie_invullen
from openaec_storage.tests.hulp import OPSLAGINSTELLINGEN, NagebootsteS3, foutcode, gebruiker, ruim_op, ruimte, upload

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
		frappe.local.response.pop("huidig_bestand", None)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.response.pop("huidige_versie", None)
		frappe.local.response.pop("huidig_bestand", None)
		frappe.db.delete("Opslagvergrendeling")
		self.s3.stop()
		ruim_op()

	def huidige_versie_in_antwoord(self):
		return frappe.local.response.pop("huidige_versie", None)

	def basis_in_antwoord(self) -> tuple:
		"""`huidige_versie` en `huidig_bestand` uit het antwoord; beide moeten er staan (ook als ze null zijn)."""
		self.assertIn("huidige_versie", frappe.local.response)
		self.assertIn("huidig_bestand", frappe.local.response)
		return frappe.local.response.pop("huidige_versie"), frappe.local.response.pop("huidig_bestand")

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

	# --- versienummers gaan alleen omhoog en komen nooit terug -------------------------------------------

	def planning_met_twee_versies(self) -> str:
		"""Anna's planning.ifc (F1): versie 1, en versie 2 met inhoud A."""
		f1 = upload(self.ruimte, "planning.ifc", b"een")["bestand"]
		upload(self.ruimte, "planning.ifc", b"inhoud A", basis_versie=1)
		return f1

	def piet_verplaatst_over_een_ander(self, f1: str, herstellen: bool = True) -> None:
		"""Piet verplaatst F1 met overschrijven over ander.ifc: versie 2 gaat naar ander.ifc, F1 staat als versie 1 in
		de prullenbak. Daarna herstelt hij F1."""
		frappe.set_user(self.piet)
		try:
			upload(self.ruimte, "ander.ifc", b"ander")
			api.verplaats(bestand=f1, naam="ander.ifc", overschrijf=1)
			if herstellen:
				api.herstel(bestand=f1)
		finally:
			frappe.set_user(self.anna)

	def versies_van(self, bestand: str) -> tuple:
		return frappe.db.get_value("Opslagbestand", bestand, ["huidige_versie", "hoogste_versie"])

	def test_versienummer_komt_na_verplaatsen_en_herstellen_niet_terug(self):
		f1 = self.planning_met_twee_versies()
		# Anna begint haar upload terwijl F1 nog op versie 2 staat (de vroege controle laat hem door).
		met_weiger = start(self.ruimte, "planning.ifc", b"oud werk van anna", basis_versie=2, basis_bestand=f1, bij_conflict="weiger")
		self.piet_verplaatst_over_een_ander(f1, herstellen=False)
		self.assertEqual(self.versies_van(f1), (1, 2))
		frappe.set_user(self.piet)
		api.herstel(bestand=f1)
		b = upload(self.ruimte, "planning.ifc", b"inhoud B", basis_versie=1)
		frappe.set_user(self.anna)
		# B wordt versie 3, niet opnieuw versie 2.
		self.assertEqual((b["bestand"], b["versie"], b["conflict"]), (f1, 3, False))
		self.assertEqual(self.volgnummers(f1), [1, 3])
		# Anna werkte offline vanuit F1 versie 2 (inhoud A). Die versie ging met het verplaatsen naar ander.ifc: het paar
		# (F1, 2) bestaat niet meer, dus de basis is onbekend. Bij het afronden, bij het begin en in upload_klein.
		self.assertEqual(foutcode(self, api.OnbekendeBasis, api.upload_klaar, **met_weiger), "onbekende_basis")
		self.assertEqual(self.basis_in_antwoord(), (3, f1))
		code = foutcode(
			self, api.OnbekendeBasis, start, self.ruimte, "planning.ifc", b"oud werk van anna", basis_versie=2, basis_bestand=f1, bij_conflict="weiger"
		)
		self.assertEqual(code, "onbekende_basis")
		self.assertEqual(self.basis_in_antwoord(), (3, f1))
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("planning.ifc", b"oud", basis_versie=2, basis_bestand=f1, bij_conflict="weiger")], {"f0": b"oud"}
		)["resultaten"]
		self.assertEqual((k["ok"], k["foutcode"], k["huidige_versie"], k["huidig_bestand"]), (False, "onbekende_basis", 3, f1))
		# Als basis downloaden weigert ook; gewoon downloaden zegt, zoals altijd, dat de versie niet bestaat.
		self.assertEqual(foutcode(self, api.OnbekendeBasis, api.download, bestand=f1, versie=2, als_basis=1), "onbekende_basis")
		with self.assertRaises(frappe.DoesNotExistError):
			api.download(bestand=f1, versie=2)
		self.assertEqual(api.download(bestand=f1, versie=3, als_basis=1)["versie"], 3)
		r = api.upload_klaar(**start(self.ruimte, "planning.ifc", b"oud werk van anna", basis_versie=2, basis_bestand=f1))
		self.assertTrue(r["conflict"])
		self.assertNotEqual(r["bestand"], f1)
		self.assertEqual(self.volgnummers(f1), [1, 3])
		self.assertEqual(self.inhoud(f1, 3), b"inhoud B")

	def test_paar_dat_voor_de_patch_al_hergebruikt_was_is_een_onbekende_basis(self):
		"""Anna werkt offline vanuit (F1, 2) = A. Vóór de invoering verplaatst Piet F1 met overschrijven, herstelt het en
		uploadt B: weer (F1, 2). Na de patch is de huidige versie nog 2 (B), maar Anna's basis (F1, 2) is onzeker."""
		f1 = self.planning_met_twee_versies()
		self.piet_verplaatst_over_een_ander(f1)
		# De oude route, zonder hoogste_versie: B kreeg nummer 2 opnieuw.
		frappe.db.sql("update `tabOpslagbestand` set hoogste_versie = 0, basisgrens = 0 where ruimte = %s", self.ruimte)
		frappe.set_user(self.piet)
		b = upload(self.ruimte, "planning.ifc", b"inhoud B", basis_versie=1)
		frappe.set_user(self.anna)
		self.assertEqual((b["bestand"], b["versie"]), (f1, 2))
		hoogste_versie_invullen.execute()
		self.assertEqual(
			frappe.db.get_value("Opslagbestand", f1, ["huidige_versie", "hoogste_versie", "basisgrens"]), (2, 3, 4)
		)
		# Met weiger: onbekende_basis, ook al is (F1, 2) de huidige versie. Bij het begin en in upload_klein.
		code = foutcode(
			self, api.OnbekendeBasis, start, self.ruimte, "planning.ifc", b"inhoud A+", basis_versie=2, basis_bestand=f1, bij_conflict="weiger"
		)
		self.assertEqual(code, "onbekende_basis")
		self.assertEqual(self.basis_in_antwoord(), (2, f1))
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("planning.ifc", b"A+", basis_versie=2, basis_bestand=f1, bij_conflict="weiger")], {"f0": b"A+"}
		)["resultaten"]
		self.assertEqual((k["ok"], k["foutcode"], k["huidige_versie"], k["huidig_bestand"]), (False, "onbekende_basis", 2, f1))
		# Standaard (kopie): een conflictkopie, en B blijft staan.
		r = api.upload_klaar(**start(self.ruimte, "planning.ifc", b"inhoud A+", basis_versie=2, basis_bestand=f1))
		(k,) = api.upload_klein_bytes(
			self.ruimte, [item("planning.ifc", b"A++", basis_versie=2, basis_bestand=f1)], {"f0": b"A++"}
		)["resultaten"]
		for kopie in (r, k):
			self.assertTrue(kopie["conflict"])
			self.assertNotEqual(kopie["bestand"], f1)
		self.assertEqual(self.volgnummers(f1), [1, 2])
		self.assertEqual(self.inhoud(f1), b"inhoud B")
		# Als basis downloaden weigert; gewoon downloaden blijft werken (de geschiedenis blijft leesbaar).
		self.assertEqual(foutcode(self, api.OnbekendeBasis, api.download, bestand=f1, versie=2, als_basis=1), "onbekende_basis")
		self.assertEqual(foutcode(self, api.OnbekendeBasis, api.download, bestand=f1, als_basis=1), "onbekende_basis")
		self.assertEqual(self.inhoud(f1, 2), b"inhoud B")
		# Een oude client zonder basis_bestand: zoals voorheen een nieuwe versie, met het nummer na het hoogste. Die
		# versie ligt boven de grens en is een zekere basis.
		r = api.upload_klaar(**start(self.ruimte, "planning.ifc", b"oude client", basis_versie=2))
		self.assertEqual((r["bestand"], r["versie"], r["conflict"]), (f1, 4, False))
		self.assertEqual(api.download(bestand=f1, als_basis=1)["versie"], 4)
		r = api.upload_klaar(**start(self.ruimte, "planning.ifc", b"vijf", basis_versie=4, basis_bestand=f1, bij_conflict="weiger"))
		self.assertEqual((r["bestand"], r["versie"], r["conflict"]), (f1, 5, False))

	def test_bestand_zonder_verwijderd_krijgt_grens_0_en_een_oude_basis_blijft_geldig(self):
		g = upload(self.ruimte, "schema.ifc", b"een")["bestand"]
		upload(self.ruimte, "schema.ifc", b"twee", basis_versie=1)
		frappe.db.sql("update `tabOpslagbestand` set hoogste_versie = 0, basisgrens = 0 where name = %s", g)
		hoogste_versie_invullen.execute()
		self.assertEqual(frappe.db.get_value("Opslagbestand", g, ["huidige_versie", "hoogste_versie", "basisgrens"]), (2, 2, 0))
		self.assertEqual(api.download(bestand=g, versie=1, als_basis=1)["versie"], 1)
		code = foutcode(
			self, api.VerouderdeBasis, start, self.ruimte, "schema.ifc", b"x", basis_versie=1, basis_bestand=g, bij_conflict="weiger"
		)
		self.assertEqual(code, "verouderde_basis")
		self.assertEqual(self.basis_in_antwoord(), (2, g))
		r = api.upload_klaar(**start(self.ruimte, "schema.ifc", b"drie", basis_versie=2, basis_bestand=g, bij_conflict="weiger"))
		self.assertEqual((r["bestand"], r["versie"], r["conflict"]), (g, 3, False))

	def test_verplaatsen_met_overschrijven_geeft_het_doel_het_nummer_na_het_hoogste(self):
		f1 = self.planning_met_twee_versies()
		self.piet_verplaatst_over_een_ander(f1)
		self.assertEqual(self.versies_van(f1), (1, 2))
		los = upload(self.ruimte, "los.ifc", b"los")["bestand"]
		r = api.verplaats(bestand=los, naam="planning.ifc", overschrijf=1)
		self.assertEqual((r["bestand"], r["versie"]), (f1, 3))
		self.assertEqual(self.volgnummers(f1), [1, 3])
		self.assertEqual(self.versies_van(f1), (3, 3))

	def test_hoogste_versie_en_basisgrens_veranderen_niet_bij_opslaan(self):
		"""Ook wie het bestand als document opslaat (bijv. via /api/resource), kan `hoogste_versie` niet verlagen en de
		`basisgrens` niet veranderen."""
		f1 = self.planning_met_twee_versies()
		frappe.db.set_value("Opslagbestand", f1, "basisgrens", 2)
		frappe.set_user(self.piet)
		doc = frappe.get_doc("Opslagbestand", f1)
		doc.huidige_versie, doc.hoogste_versie, doc.basisgrens = 1, 0, 0
		doc.save()
		frappe.set_user(self.anna)
		self.assertEqual(self.versies_van(f1), (1, 2))
		self.assertEqual(frappe.db.get_value("Opslagbestand", f1, "basisgrens"), 2)
		self.assertEqual(upload(self.ruimte, "planning.ifc", b"drie", basis_versie=1)["versie"], 3)

	def test_patch_geeft_een_al_verplaatst_bestand_een_veilige_bovengrens(self):
		f1 = self.planning_met_twee_versies()
		self.piet_verplaatst_over_een_ander(f1, herstellen=False)
		ander = self.actief("ander.ifc")
		gewoon = upload(self.ruimte, "gewoon.ifc", b"g")["bestand"]
		api.verwijder(bestand=gewoon)
		api.herstel(bestand=gewoon)
		stil = upload(self.ruimte, "stil.ifc", b"s")["bestand"]
		ander_id = frappe.db.get_value("Opslagbestand", {"ruimte": self.ruimte, "naam": "ander.ifc"}, "name")
		# Zoals vóór deze versie: geen hoogste_versie (alleen de database, zonder het document).
		frappe.db.sql("update `tabOpslagbestand` set hoogste_versie = 0 where ruimte = %s", self.ruimte)
		hoogste_versie_invullen.execute()
		# F1: huidige 1 + één keer verplaatst = 2. Gewoon verwijderd telt ook mee (te veel, dus veilig). Een bestand met
		# een regel `verwijderd` krijgt als grens het eerste nummer na de patch; de andere 0.
		self.assertEqual(
			[self.versies_van(b) for b in (f1, gewoon, stil, ander_id)], [(1, 2), (1, 2), (1, 1), (2, 2)]
		)
		self.assertEqual(
			[frappe.db.get_value("Opslagbestand", b, "basisgrens") for b in (f1, gewoon, stil, ander_id)], [3, 3, 0, 0]
		)
		self.assertEqual(ander, ["ander.ifc"])
		frappe.set_user(self.piet)
		api.herstel(bestand=f1)
		b = upload(self.ruimte, "planning.ifc", b"inhoud B", basis_versie=1)
		frappe.set_user(self.anna)
		self.assertEqual((b["bestand"], b["versie"]), (f1, 3))
		# Nog een keer draaien verlaagt niets (het kan hoogstens een nummer overslaan).
		hoogste_versie_invullen.execute()
		self.assertGreaterEqual(self.versies_van(f1)[1], 3)


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

	def __init__(self):
		self.origineel = api._registreer
		self.a_vastgelegd = threading.Event()
		self.a_door = threading.Event()
		self.b_bij_vastleggen = threading.Event()

	def registreer(self, info: dict) -> dict:
		naam = threading.current_thread().name
		if naam == "B":
			self.b_bij_vastleggen.set()
		uit = self.origineel(info)
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

	def wissel(self, functie_a, functie_b, gebruiker_b: str | None = None, b_langs_registreer: bool = True):
		"""A (Anna) en B (standaard Piet) in de vaste volgorde van `Wissel`. Geeft beide verzoeken en de stand van B
		vlak voordat A commit. Gaat B niet langs `api._registreer` (de uploadlink), dan krijgt hij alleen de bedenktijd."""
		frappe.db.commit()
		w = Wissel()
		a = Verzoek("A", self, self.anna, functie_a)
		b = Verzoek("B", self, gebruiker_b or self.piet, functie_b)
		stand = {"klaar": None, "wacht_op": None}
		with mock.patch.object(api, "_registreer", w.registreer):
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


def cint_of(waarde) -> int:
	return int(waarde or 0)
