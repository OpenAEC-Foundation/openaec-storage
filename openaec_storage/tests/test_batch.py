import base64

import frappe
import requests
from frappe.tests import IntegrationTestCase

from openaec_storage import api, ruimtebeheer
from openaec_storage.tests.hulp import NagebootsteS3, foutcode, gebruiker, ruim_op, ruimte, upload

KB = 1000
MIB = 1024 * 1024


def item(naam: str, inhoud: bytes, **extra) -> dict:
	return {"naam": naam, "grootte": len(inhoud), "blokhash": "h-" + naam, **extra}


class TestBatch(IntegrationTestCase):
	"""Veel kleine bestanden in één verzoek: dezelfde regels als de upload per bestand, met een antwoord per bestand."""

	def setUp(self):
		ruim_op()
		self.s3 = NagebootsteS3().start()
		self.beheerder = gebruiker("beheerder")
		self.schrijver = gebruiker("schrijver")
		self.lezer = gebruiker("lezer")
		self.buiten = gebruiker("buiten")
		self.ruimte = ruimte(
			"Project Klein", {self.beheerder: "beheerder", self.schrijver: "schrijver", self.lezer: "lezer"}
		)
		frappe.set_user(self.schrijver)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.s3.stop()
		ruim_op()

	def upload(self, *bestanden: tuple[dict, bytes]) -> list[dict]:
		inhoud = {f"f{i}": data for i, (_, data) in enumerate(bestanden)}
		return api.upload_klein_bytes(self.ruimte, [b for b, _ in bestanden], inhoud)["resultaten"]

	def inhoud(self, bestand: str) -> bytes:
		return requests.get(api.download(bestand=bestand)["url"], timeout=30).content

	def test_mogelijkheden_noemen_de_grenzen(self):
		m = api.mogelijkheden()
		self.assertEqual(m["upload_klein"], {"max_bestand": MIB, "max_aantal": 64, "max_bytes": 16 * MIB})
		self.assertEqual(m["download_klein"], m["upload_klein"])
		self.assertTrue(m["boom"])

	def test_veel_bestanden_in_een_verzoek_met_mappen_uit_het_pad(self):
		r = self.upload(
			(item("a.txt", b"aaa"), b"aaa"),
			(item("b.txt", b"bb", pad="Tekeningen/2026"), b"bb"),
			(item("c.txt", b"c", pad="Tekeningen/2026"), b"c"),
			(item("d.txt", b"", pad="tekeningen"), b""),
		)
		self.assertTrue(all(x["ok"] for x in r), r)
		self.assertEqual([x["index"] for x in r], [0, 1, 2, 3])
		self.assertEqual([x["versie"] for x in r], [1, 1, 1, 1])
		self.assertIsNone(r[0]["map"])
		self.assertEqual(r[1]["map"], r[2]["map"])
		# Een bestaande map wordt hergebruikt, ongeacht hoofdletters (zoals maak_map).
		tekeningen = frappe.db.get_value("Opslagmap", r[1]["map"], "bovenliggende")
		self.assertEqual(r[3]["map"], tekeningen)
		self.assertEqual(frappe.db.count("Opslagmap", {"ruimte": self.ruimte}), 2)
		self.assertEqual(self.inhoud(r[1]["bestand"]), b"bb")
		self.assertEqual(api.blokken(bestand=r[0]["bestand"])["hashes"], ["h-a.txt"])
		soorten = frappe.get_all("Opslagwijziging", filters={"ruimte": self.ruimte}, pluck="soort")
		self.assertEqual(sorted(soorten), ["nieuw"] * 6)

	def test_versies_en_conflicten_zoals_bij_de_gewone_upload(self):
		eerste = upload(self.ruimte, "plan.txt", b"een")
		r = self.upload(
			(item("plan.txt", b"twee", basis_versie=eerste["versie"]), b"twee"),
		)[0]
		self.assertEqual((r["bestand"], r["versie"], r["conflict"]), (eerste["bestand"], 2, False))
		# Verouderde basis: conflictkopie naast het origineel, niets gaat verloren.
		r = self.upload((item("plan.txt", b"drie", basis_versie=1), b"drie"))[0]
		self.assertTrue(r["conflict"])
		self.assertIn("(conflict", r["naam"])
		self.assertEqual(self.inhoud(eerste["bestand"]), b"twee")
		self.assertEqual(self.inhoud(r["bestand"]), b"drie")

	def test_een_fout_per_bestand_stopt_de_rest_niet_en_laat_niets_half_staan(self):
		map = api.maak_map(ruimte=self.ruimte, naam="Tekeningen")["map"]
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.append("mapregels", {"map": map, "pad": "Tekeningen", "extensies": "pdf"})
		doc.save()
		frappe.set_user(self.schrijver)
		r = self.upload(
			(item("goed.pdf", b"pdf", map=map), b"pdf"),
			(item("fout.txt", b"txt", map=map), b"txt"),
			(item("../kapot", b"x"), b"x"),
			({"naam": "verkeerd.txt", "grootte": 10}, b"kort"),
			(item("ook goed.txt", b"ok"), b"ok"),
		)
		self.assertEqual([x["ok"] for x in r], [True, False, False, False, True])
		self.assertEqual(r[1]["foutcode"], "extensie_niet_toegestaan")
		self.assertIn("pdf", r[1]["fout"])
		self.assertIn("grootte", r[3]["fout"])
		self.assertEqual(
			sorted(frappe.get_all("Opslagbestand", filters={"ruimte": self.ruimte}, pluck="naam")), ["goed.pdf", "ook goed.txt"]
		)
		self.assertEqual(frappe.db.count("Bestandsversie", {"ruimte": self.ruimte}), 2)
		self.assertNotIn("_server_messages", frappe.local.response)

	def test_quotum_van_de_ruimte_telt_binnen_het_verzoek_op(self):
		frappe.db.set_value("Ruimte", self.ruimte, "quotum_gb", 10 * KB / 1_000_000_000)
		data = b"x" * 6 * KB
		r = self.upload((item("een.bin", data), data), (item("twee.bin", data), data), (item("klein.bin", b"k"), b"k"))
		self.assertEqual([x["ok"] for x in r], [True, False, True])
		self.assertEqual(r[1]["foutcode"], "quotum_ruimte_vol")
		self.assertIn("quotum", r[1]["fout"])

	def test_quotum_van_de_pool(self):
		frappe.local.conf["openaec_storage_quotum_gb"] = 5 * KB / 1_000_000_000
		data = b"x" * 6 * KB
		self.assertEqual(self.upload((item("groot.bin", data), data))[0]["foutcode"], "quotum_pool_vol")

	def test_rechten_per_bestand(self):
		frappe.set_user(self.lezer)
		r = self.upload((item("a.txt", b"a"), b"a"))[0]
		self.assertFalse(r["ok"])
		frappe.set_user(self.buiten)
		self.assertFalse(self.upload((item("a.txt", b"a"), b"a"))[0]["ok"])
		self.assertEqual(frappe.db.count("Opslagbestand", {"ruimte": self.ruimte}), 0)

	def test_alleen_lezenmap_en_gearchiveerde_ruimte(self):
		map = api.maak_map(ruimte=self.ruimte, naam="Huisstijl")["map"]
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.append("mapregels", {"map": map, "pad": "Huisstijl", "alleen_lezen": 1})
		doc.save()
		frappe.set_user(self.schrijver)
		r = self.upload((item("logo.png", b"png", map=map), b"png"), (item("los.txt", b"t", pad="Huisstijl/Nieuw"), b"t"))
		self.assertEqual([x["foutcode"] for x in r], ["map_alleen_lezen", "map_alleen_lezen"])
		frappe.set_user(self.beheerder)
		self.assertTrue(self.upload((item("logo.png", b"png", map=map), b"png"))[0]["ok"])

		ruimtebeheer.archiveer(ruimte=self.ruimte)
		frappe.set_user(self.schrijver)
		self.assertEqual(self.upload((item("na.txt", b"n"), b"n"))[0]["foutcode"], "ruimte_gearchiveerd")
		# Lezen blijft werken.
		bestand = frappe.db.get_value("Opslagbestand", {"ruimte": self.ruimte, "naam": "logo.png"})
		self.assertTrue(api.download_klein(bestanden=[bestand])["bestanden"][0]["ok"])

	def test_grenzen_van_een_verzoek(self):
		te_veel = [(item(f"{i}.txt", b"x"), b"x") for i in range(65)]
		self.assertEqual(foutcode(self, frappe.ValidationError, self.upload, *te_veel), "batch_te_groot")
		groot = b"x" * (MIB + 1)
		r = self.upload((item("groot.bin", groot), groot), (item("klein.bin", b"k"), b"k"))
		self.assertEqual(r[0]["foutcode"], "te_groot_voor_batch")
		self.assertTrue(r[1]["ok"])

	def test_ontbrekende_inhoud(self):
		r = api.upload_klein_bytes(self.ruimte, [item("a.txt", b"a"), item("b.txt", b"b", veld="tweede")], {"tweede": b"b"})
		self.assertEqual([x["ok"] for x in r["resultaten"]], [False, True])

	def test_download_klein_geeft_de_inhoud_per_bestand(self):
		a = upload(self.ruimte, "a.txt", b"inhoud a")
		b = upload(self.ruimte, "b.txt", b"inhoud b")
		b2 = upload(self.ruimte, "b.txt", b"inhoud b, versie 2", basis_versie=b["versie"])
		frappe.set_user(self.lezer)
		r = api.download_klein(bestanden=[a["bestand"], {"bestand": b["bestand"], "versie": 1}, b2["bestand"], "bestaat-niet"])
		uit = r["bestanden"]
		self.assertEqual([x["ok"] for x in uit], [True, True, True, False])
		self.assertEqual(base64.b64decode(uit[0]["inhoud"]), b"inhoud a")
		self.assertEqual((base64.b64decode(uit[1]["inhoud"]), uit[1]["versie"]), (b"inhoud b", 1))
		self.assertEqual((base64.b64decode(uit[2]["inhoud"]), uit[2]["versie"]), (b"inhoud b, versie 2", 2))
		self.assertEqual(uit[0]["naam"], "a.txt")
		self.assertEqual(uit[3]["index"], 3)
		# Geen lid: geen inhoud.
		frappe.set_user(self.buiten)
		self.assertFalse(api.download_klein(bestanden=[a["bestand"]])["bestanden"][0]["ok"])

	def test_download_klein_grenzen(self):
		frappe.set_user(self.schrijver)
		groot = upload(self.ruimte, "groot.bin", b"x" * (MIB + 1))
		klein = upload(self.ruimte, "klein.bin", b"k")
		uit = api.download_klein(bestanden=[groot["bestand"], klein["bestand"]])["bestanden"]
		self.assertEqual(uit[0]["foutcode"], "te_groot_voor_batch")
		self.assertTrue(uit[1]["ok"])
		self.assertEqual(
			foutcode(self, frappe.ValidationError, api.download_klein, bestanden=[klein["bestand"]] * 65), "batch_te_groot"
		)

	def test_boom_geeft_alle_mappen_en_bestanden(self):
		r = self.upload(
			(item("a.txt", b"a"), b"a"),
			(item("b.txt", b"b", pad="X/Y"), b"b"),
			(item("c.txt", b"c", pad="X"), b"c"),
		)
		weg = upload(self.ruimte, "weg.txt", b"w")
		api.verwijder(bestand=weg["bestand"])
		frappe.set_user(self.lezer)
		boom = api.boom(ruimte=self.ruimte)
		self.assertFalse(boom["meer"])
		self.assertEqual(sorted(m["naam"] for m in boom["mappen"]), ["X", "Y"])
		self.assertEqual(sorted(b["naam"] for b in boom["bestanden"]), ["a.txt", "b.txt", "c.txt"])
		b = next(x for x in boom["bestanden"] if x["naam"] == "b.txt")
		self.assertEqual((b["map"], b["huidige_versie"], b["grootte"]), (r[1]["map"], 1, 1))
		# Per pagina.
		eerste = api.boom(ruimte=self.ruimte, start=0, aantal=2)
		tweede = api.boom(ruimte=self.ruimte, start=2, aantal=2)
		self.assertTrue(eerste["meer"])
		self.assertFalse(tweede["meer"])
		self.assertEqual(len(eerste["bestanden"]) + len(tweede["bestanden"]), 3)
		frappe.set_user(self.buiten)
		with self.assertRaises(frappe.PermissionError):
			api.boom(ruimte=self.ruimte)

	def test_maak_mappen_in_een_verzoek(self):
		bestaand = api.maak_map(ruimte=self.ruimte, naam="Tekeningen")["map"]
		r = api.maak_mappen(ruimte=self.ruimte, paden=["tekeningen/2026/Maart", "Notities", "/Notities/", "nul", "Notities/con"])
		self.assertEqual(sorted(r["mappen"]), ["Notities", "tekeningen/2026/Maart"])
		self.assertEqual(frappe.db.get_value("Opslagmap", r["mappen"]["tekeningen/2026/Maart"], "naam"), "Maart")
		maart = frappe.db.get_value("Opslagmap", r["mappen"]["tekeningen/2026/Maart"], "bovenliggende")
		self.assertEqual(frappe.db.get_value("Opslagmap", maart, "bovenliggende"), bestaand)
		self.assertEqual({p: f["foutcode"] for p, f in r["fouten"].items()}, {"nul": "ongeldige_naam", "Notities/con": "ongeldige_naam"})
		# Nog eens: niets nieuws.
		aantal = frappe.db.count("Opslagmap", {"ruimte": self.ruimte})
		self.assertEqual(api.maak_mappen(ruimte=self.ruimte, paden=["Notities", "Tekeningen/2026"])["fouten"], {})
		self.assertEqual(frappe.db.count("Opslagmap", {"ruimte": self.ruimte}), aantal)
		frappe.set_user(self.lezer)
		self.assertIn("Lezer", api.maak_mappen(ruimte=self.ruimte, paden=["Lezer"])["fouten"])

	def test_sha256_wordt_gecontroleerd(self):
		import hashlib

		goed = hashlib.sha256(b"abc").hexdigest()
		r = self.upload((item("a.txt", b"abc", sha256=goed), b"abc"), (item("b.txt", b"abd", sha256=goed), b"abd"))
		self.assertEqual([x["ok"] for x in r], [True, False])
		self.assertIn("SHA-256", r[1]["fout"])
		self.assertEqual(frappe.db.get_value("Bestandsversie", {"bestand": r[0]["bestand"]}, "sha256"), goed)
