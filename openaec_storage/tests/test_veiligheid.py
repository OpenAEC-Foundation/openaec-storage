import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage import api, veiligheid
from openaec_storage.tests.hulp import NagebootsteS3, foutcode, gebruiker, met_rol, ruim_op, ruimte, upload


def grenzen(**per_actie) -> dict:
	return {actie: per_actie.get(actie, {"per_keer": 0, "per_tijdvak": 0}) for actie in veiligheid.ACTIES}


class TestVeiligheid(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		frappe.db.delete("Opslagbulkactie")
		frappe.db.set_default(veiligheid.SLEUTEL_BLOKKADES, "{}")
		frappe.cache.delete_keys(veiligheid.TELLER)
		frappe.cache.delete_keys(veiligheid.MELDING_GEDAAN)
		self.s3 = NagebootsteS3().start()
		self.schrijver = gebruiker("schrijver")
		self.beheerder = met_rol(gebruiker("beheerder"), "System Manager")
		self.ruimte = ruimte("Project Veilig", {self.schrijver: "schrijver"})
		frappe.set_user(self.schrijver)
		self.map = api.maak_map(ruimte=self.ruimte, naam="Tekeningen")["map"]

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.set_default(veiligheid.SLEUTEL_INSTELLINGEN, None)
		frappe.db.set_default(veiligheid.SLEUTEL_BLOKKADES, "{}")
		frappe.db.delete("Opslagbulkactie")
		frappe.cache.delete_keys(veiligheid.TELLER)
		frappe.cache.delete_keys(veiligheid.MELDING_GEDAAN)
		self.s3.stop()
		ruim_op()

	def zet(self, **waarden):
		frappe.set_user("Administrator")
		veiligheid.zet_instellingen({"aan": 1, **waarden})
		frappe.set_user(self.schrijver)

	def bestanden(self, aantal: int) -> list[str]:
		return [upload(self.ruimte, f"b{i}.txt", b"x")["bestand"] for i in range(aantal)]

	def test_standaard_uit_en_dan_geen_grens(self):
		self.assertEqual(veiligheid.instellingen()["aan"], 0)
		ids = self.bestanden(3)
		uit = api.verwijder_bulk(bestanden=ids)
		self.assertTrue(all(r["ok"] for r in uit["resultaten"]))
		# Een uitgevoerde bulkactie staat in het logboek, ook met de grenzen uit.
		self.assertEqual(frappe.db.get_value("Opslagbulkactie", uit["bulkactie"], "uitkomst"), "uitgevoerd")

	def test_grens_per_keer_weigert_de_hele_bulkverwijdering_en_logt_de_poging(self):
		# Klaar-criterium: grens 50, een bulkverwijdering van 200 bestanden wordt geweigerd en staat in het logboek.
		self.zet(standaard=grenzen(verwijderen={"per_keer": 50, "per_tijdvak": 0}))
		ids = [f"onbekend-{i}" for i in range(195)] + self.bestanden(5)
		frappe.local.response.pop("foutcode", None)
		with self.assertRaises(frappe.PermissionError) as fout:
			api.verwijder_bulk(bestanden=ids)
		self.assertEqual(frappe.local.response.pop("foutcode", None), "bulk_grens")
		self.assertIn("hoogstens 50 bestanden in één keer verwijderen; dit waren er 200", str(fout.exception))
		frappe.clear_messages()
		self.assertEqual(frappe.db.count("Opslagbestand", {"ruimte": self.ruimte, "verwijderd_op": ("is", "set")}), 0)
		log = frappe.get_all("Opslagbulkactie", fields=["gebruiker", "actie", "aantal", "uitkomst", "reden", "grens"])
		self.assertEqual(len(log), 1)
		self.assertEqual(
			dict(log[0]),
			{"gebruiker": self.schrijver, "actie": "verwijderen", "aantal": 200, "uitkomst": "geweigerd", "reden": "per_keer", "grens": 50},
		)
		# De beheerder krijgt een melding.
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": self.beheerder, "document_type": "Opslagbulkactie"}))

	def test_binnen_de_grens_gaat_het_door(self):
		self.zet(standaard=grenzen(verwijderen={"per_keer": 5, "per_tijdvak": 0}))
		uit = api.verwijder_bulk(bestanden=self.bestanden(5))
		self.assertEqual(sum(r["ok"] for r in uit["resultaten"]), 5)

	def test_een_map_telt_met_alles_erin(self):
		for i in range(4):
			upload(self.ruimte, f"t{i}.dwg", b"x", map_naam=self.map)
		self.zet(standaard=grenzen(verwijderen={"per_keer": 3, "per_tijdvak": 0}))
		self.assertEqual(foutcode(self, frappe.PermissionError, api.verwijder_map, map=self.map), "bulk_grens")
		self.assertFalse(frappe.db.get_value("Opslagmap", self.map, "verwijderd_op"))

	def test_per_tijdvak_ook_bij_losse_aanroepen(self):
		# Een client die per bestand een aanroep doet (sync, WebDAV, een script), houdt ook op bij de grens.
		ids = self.bestanden(4)
		self.zet(standaard=grenzen(verwijderen={"per_keer": 0, "per_tijdvak": 3}))
		for b in ids[:3]:
			api.verwijder(bestand=b)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.verwijder, bestand=ids[3]), "bulk_grens_tijdvak")
		self.assertFalse(frappe.db.get_value("Opslagbestand", ids[3], "verwijderd_op"))
		# Nog een poging: geen tweede logregel in hetzelfde tijdvak.
		foutcode(self, frappe.PermissionError, api.verwijder, bestand=ids[3])
		self.assertEqual(frappe.db.count("Opslagbulkactie", {"reden": "per_tijdvak"}), 1)

	def test_verplaatsen_en_hernoemen_tellen_mee(self):
		ids = self.bestanden(3)
		self.zet(standaard=grenzen(verplaatsen={"per_keer": 2, "per_tijdvak": 0}))
		items = [{"soort": "bestand", "id": b} for b in ids]
		self.assertEqual(foutcode(self, frappe.PermissionError, api.verplaats_bulk, items=items, map=self.map), "bulk_grens")
		uit = api.verplaats_bulk(items=items[:2], map=self.map)
		self.assertTrue(all(r["ok"] for r in uit["resultaten"]))

	def test_bewerken_een_nieuwe_versie_telt(self):
		b = upload(self.ruimte, "rapport.docx", b"een")
		self.zet(standaard=grenzen(bewerken={"per_keer": 0, "per_tijdvak": 1}))
		upload(self.ruimte, "rapport.docx", b"twee", basis_versie=1)
		self.assertEqual(
			foutcode(self, frappe.PermissionError, upload, self.ruimte, "rapport.docx", b"drie", basis_versie=2), "bulk_grens_tijdvak"
		)
		# Een nieuw bestand is geen bewerking.
		upload(self.ruimte, "nieuw.docx", b"x")
		self.assertEqual(frappe.db.get_value("Opslagbestand", b["bestand"], "huidige_versie"), 2)

	def test_regel_per_rol_ruimer_dan_de_standaard(self):
		met_rol(self.schrijver, "Opslag Ruim")
		self.zet(
			standaard=grenzen(verwijderen={"per_keer": 1, "per_tijdvak": 0}),
			regels=[{"soort": "rol", "naam": "Opslag Ruim", "grenzen": grenzen(verwijderen={"per_keer": 10, "per_tijdvak": 0})}],
		)
		self.assertEqual(veiligheid.grenzen_voor(self.schrijver)["verwijderen"]["per_keer"], 10)
		uit = api.verwijder_bulk(bestanden=self.bestanden(3))
		self.assertTrue(all(r["ok"] for r in uit["resultaten"]))

	def test_regel_per_groep_via_de_hook(self):
		self.zet(
			standaard=grenzen(verwijderen={"per_keer": 0, "per_tijdvak": 0}),
			regels=[{"soort": "groep", "naam": "Externen", "grenzen": grenzen(verwijderen={"per_keer": 2, "per_tijdvak": 0})}],
		)
		oud = veiligheid.groepen_van
		veiligheid.groepen_van = lambda g: {"Externen"} if g == self.schrijver else set()
		try:
			self.assertEqual(veiligheid.grenzen_voor(self.schrijver)["verwijderen"]["per_keer"], 2)
			self.assertEqual(veiligheid.grenzen_voor(self.beheerder)["verwijderen"]["per_keer"], 0)
		finally:
			veiligheid.groepen_van = oud

	def test_geblokkeerd_account_weigert_ook_zonder_grenzen(self):
		b = self.bestanden(1)[0]
		frappe.set_user("Administrator")
		veiligheid.blokkeer(self.schrijver, 2)
		frappe.set_user(self.schrijver)
		self.assertEqual(foutcode(self, frappe.PermissionError, api.verwijder, bestand=b), "account_geblokkeerd")
		frappe.set_user("Administrator")
		veiligheid.deblokkeer(self.schrijver)
		frappe.set_user(self.schrijver)
		api.verwijder(bestand=b)

	def test_terugdraaien_van_verwijderen_en_verplaatsen(self):
		ids = self.bestanden(2)
		upload(self.ruimte, "in.dwg", b"x", map_naam=self.map)
		weg = api.verwijder_bulk(bestanden=ids, mappen=[self.map])
		self.assertTrue(all(r["ok"] for r in weg["resultaten"]))
		frappe.set_user("Administrator")
		uit = veiligheid.draai_terug(weg["bulkactie"])
		self.assertEqual(uit, {"gelukt": 3, "mislukt": []})
		self.assertEqual(frappe.db.count("Opslagbestand", {"ruimte": self.ruimte, "verwijderd_op": ("is", "set")}), 0)
		self.assertFalse(frappe.db.get_value("Opslagmap", self.map, "verwijderd_op"))
		self.assertEqual(frappe.db.get_value("Opslagbulkactie", weg["bulkactie"], "uitkomst"), "teruggedraaid")

		frappe.set_user(self.schrijver)
		weg = api.verplaats_bulk(items=[{"soort": "bestand", "id": b} for b in ids], map=self.map)
		self.assertEqual(frappe.db.get_value("Opslagbestand", ids[0], "map"), self.map)
		frappe.set_user("Administrator")
		veiligheid.draai_terug(weg["bulkactie"])
		self.assertIsNone(frappe.db.get_value("Opslagbestand", ids[0], "map"))
		with self.assertRaises(frappe.ValidationError):
			veiligheid.draai_terug(weg["bulkactie"])

	def test_instellingen_worden_gecontroleerd(self):
		with self.assertRaises(frappe.ValidationError):
			veiligheid.schoon({"regels": [{"soort": "rol", "naam": ""}]})
		with self.assertRaises(frappe.ValidationError):
			veiligheid.schoon({"venster_minuten": 100000})
		schoon = veiligheid.schoon({"aan": "1", "standaard": {"verwijderen": {"per_keer": "-3", "per_tijdvak": "7"}}})
		self.assertEqual(schoon["standaard"]["verwijderen"], {"per_keer": 0, "per_tijdvak": 7})
		self.assertEqual(schoon["aan"], 1)
