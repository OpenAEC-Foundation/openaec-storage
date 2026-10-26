"""Versturen van bestanden en mappen die al in de opslag staan: server-side kopiëren in plaats van opnieuw uploaden."""

from unittest.mock import patch

import frappe
import requests
from frappe.utils import add_to_date, getdate, now_datetime

from openaec_storage import api, overdracht, ruimtebeheer, s3
from openaec_storage.tests.hulp import foutcode, gebruiker, ruimte, upload
from openaec_storage.tests.test_overdracht import OverdrachtTest, objecten, opgeslagen_zip, stuur

MIB = 1024 * 1024


class VanOpslagTest(OverdrachtTest):
	def setUp(self):
		super().setUp()
		frappe.db.delete("Opslagdeling")
		self.map = api.maak_map(ruimte=self.ruimte, naam="Tekeningen")["map"]
		self.sub = api.maak_map(ruimte=self.ruimte, naam="2026 fase 2", bovenliggende=self.map)["map"]
		self.plan = upload(self.ruimte, "plan.pdf", b"%PDF plan", map_naam=self.map)["bestand"]
		self.detail = upload(self.ruimte, "detail.dwg", b"DWG detail", map_naam=self.sub)["bestand"]
		self.los = upload(self.ruimte, "notitie.txt", b"notitie")["bestand"]

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Opslagdeling")
		super().tearDown()

	def maak(self, **extra) -> dict:
		with patch("frappe.sendmail"):
			return overdracht.maak_van_opslag(**{"naam": "Uit de opslag", **extra})

	def zip_van(self, uitslag) -> dict:
		frappe.set_user("Guest")
		archief = opgeslagen_zip(overdracht.zip(token=uitslag["token"]))
		frappe.set_user(self.anna)
		return {naam: archief.read(naam) for naam in archief.namelist()}


class TestMaakVanOpslag(VanOpslagTest):
	def test_bestand_uit_de_opslag_zonder_upload(self):
		with patch.object(overdracht, "bestand_start", side_effect=AssertionError("geen upload")):
			uitslag = self.maak(bestanden=[self.los], ontvangers=["jan@voorbeeld.nl"])
		self.assertEqual((uitslag["status"], uitslag["bezig"], uitslag["aantal_bestanden"]), ("Actief", False, 1))
		self.assertIn("/overdracht/", uitslag["url"])
		self.assertEqual(uitslag["mail"], ["jan@voorbeeld.nl"])
		rij = frappe.get_all("Overdrachtbestand", filters={"overdracht": uitslag["overdracht"]}, fields=["*"])[0]
		self.assertEqual((rij.naam, rij.bron_bestand, rij.bron_versie, rij.klaar), ("notitie.txt", self.los, 1, 1))
		# Een eigen kopie onder de overdracht: de versie in de ruimte blijft staan als de overdracht weggaat.
		self.assertTrue(rij.s3_sleutel.startswith(s3.overdracht_map(uitslag["overdracht"])))
		frappe.set_user("Guest")
		link = overdracht.download(token=uitslag["token"], bestand=rij.name)["url"]
		self.assertEqual(requests.get(link, timeout=30).content, b"notitie")

	def test_map_gaat_mee_met_alles_eronder_en_de_mappen_in_de_zip(self):
		uitslag = self.maak(mappen=[self.map], bestanden=[self.los])
		self.assertEqual(uitslag["aantal_bestanden"], 3)
		self.assertEqual(
			self.zip_van(uitslag),
			{
				"notitie.txt": b"notitie",
				"Tekeningen/plan.pdf": b"%PDF plan",
				"Tekeningen/2026 fase 2/detail.dwg": b"DWG detail",
			},
		)
		frappe.set_user("Guest")
		paden = sorted(b["pad"] for b in overdracht.openen(token=uitslag["token"])["bestanden"])
		self.assertEqual(paden, ["Tekeningen/2026 fase 2/detail.dwg", "Tekeningen/plan.pdf", "notitie.txt"])

	def test_naam_van_het_eerste_item_als_er_geen_naam_is(self):
		uitslag = self.maak(mappen=[self.map], naam="")
		self.assertEqual(frappe.db.get_value("Overdracht", uitslag["overdracht"], "naam"), "Tekeningen")

	def test_de_gevraagde_versie_gaat_mee(self):
		upload(self.ruimte, "notitie.txt", b"notitie twee", basis_versie=1)
		uitslag = self.maak(bestanden=[{"bestand": self.los, "versie": 1}])
		rij = frappe.get_all("Overdrachtbestand", filters={"overdracht": uitslag["overdracht"]}, fields=["name", "grootte"])[0]
		self.assertEqual(rij.grootte, len(b"notitie"))
		frappe.set_user("Guest")
		link = overdracht.download(token=uitslag["token"], bestand=rij.name)["url"]
		self.assertEqual(requests.get(link, timeout=30).content, b"notitie")
		frappe.set_user(self.anna)
		# Zonder versie de huidige.
		nieuw = self.maak(bestanden=[self.los])
		self.assertEqual(nieuw["totale_grootte"], len(b"notitie twee"))

	def test_wachtwoord_en_vervaldatum(self):
		dag = str(getdate(add_to_date(now_datetime(), days=3)))
		uitslag = self.maak(bestanden=[self.los], wachtwoord="geheim123", verloopt=dag)
		self.assertEqual(str(getdate(uitslag["verloopt_op"])), dag)
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			overdracht.openen(token=uitslag["token"])
		self.assertEqual(len(overdracht.openen(token=uitslag["token"], wachtwoord="geheim123")["bestanden"]), 1)

	def test_staat_in_de_wijzigingenlijst(self):
		self.maak(mappen=[self.map], bestanden=[self.los])
		regels = frappe.get_all(
			"Opslagwijziging", filters={"soort": "verstuurd"}, fields=["ruimte", "doel_doctype", "doel", "owner"]
		)
		self.assertEqual(
			sorted((r.doel_doctype, r.doel) for r in regels), sorted([("Opslagmap", self.map), ("Opslagbestand", self.los)])
		)
		self.assertTrue(all(r.ruimte == self.ruimte and r.owner == self.anna for r in regels))

	def test_telt_mee_in_het_opslaggebruik_en_ingetrokken_blijft_het_origineel(self):
		uitslag = self.maak(bestanden=[self.los])
		self.assertEqual(overdracht.gebruikt_bytes(), len(b"notitie"))
		overdracht.trek_in(overdracht=uitslag["overdracht"])
		self.assertEqual(objecten(s3.overdracht_map(uitslag["overdracht"])), [])
		self.assertEqual(requests.get(api.download(bestand=self.los)["url"], timeout=30).content, b"notitie")


class TestRegels(VanOpslagTest):
	def test_leesrecht_is_nodig(self):
		buiten = gebruiker("buiten")
		ruimte("Eigen ruimte", {buiten: "schrijver"})
		frappe.set_user(buiten)
		with self.assertRaises(frappe.PermissionError):
			self.maak(bestanden=[self.los])
		with self.assertRaises(frappe.PermissionError):
			self.maak(mappen=[self.map])
		self.assertFalse(frappe.db.exists("Overdracht", {"owner": buiten}))

	def test_leesrecht_via_een_deling_is_genoeg(self):
		extern = gebruiker("extern")
		ruimte("Eigen ruimte", {extern: "schrijver"})
		api.deel(doel_doctype="Opslagmap", doel=self.map, soort="gebruiker", gebruiker=extern, rechten="lezen")
		frappe.set_user(extern)
		uitslag = self.maak(mappen=[self.sub])
		self.assertEqual(uitslag["aantal_bestanden"], 1)
		# Buiten de gedeelde map niet.
		with self.assertRaises(frappe.PermissionError):
			self.maak(bestanden=[self.los])

	def test_wie_niet_mag_versturen_ook_niet_uit_de_opslag(self):
		lezer = gebruiker("lezer")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.append("leden", {"gebruiker": lezer, "rol": "lezer"})
		doc.save(ignore_permissions=True)
		frappe.set_user(lezer)
		with self.assertRaises(frappe.PermissionError):
			self.maak(bestanden=[self.los])

	def test_gearchiveerde_ruimte_zoals_bij_delen(self):
		beheerder = gebruiker("beheerder")
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Ruimte", self.ruimte)
		doc.append("leden", {"gebruiker": beheerder, "rol": "beheerder"})
		doc.save(ignore_permissions=True)
		frappe.set_user(beheerder)
		ruimtebeheer.archiveer(ruimte=self.ruimte)
		frappe.set_user(self.anna)
		self.assertEqual(foutcode(self, frappe.PermissionError, self.maak, bestanden=[self.los]), "ruimte_gearchiveerd")
		self.assertEqual(foutcode(self, frappe.PermissionError, self.maak, mappen=[self.map]), "ruimte_gearchiveerd")

	def test_niets_uit_de_prullenbak(self):
		api.verwijder(bestand=self.los)
		self.assertEqual(foutcode(self, frappe.DoesNotExistError, self.maak, bestanden=[self.los]), "verwijderd")
		# Een verwijderd bestand in een map gaat niet mee; een verwijderde submap ook niet.
		api.verwijder_map(map=self.sub)
		uitslag = self.maak(mappen=[self.map])
		self.assertEqual(uitslag["aantal_bestanden"], 1)
		self.assertEqual(list(self.zip_van(uitslag)), ["Tekeningen/plan.pdf"])

	def test_lege_map_kan_niet(self):
		leeg = api.maak_map(ruimte=self.ruimte, naam="Leeg")["map"]
		with self.assertRaises(frappe.ValidationError):
			self.maak(mappen=[leeg])
		with self.assertRaises(frappe.ValidationError):
			self.maak()

	def test_grenzen_van_een_overdracht_en_het_quotum(self):
		frappe.local.conf["openaec_storage_overdracht_max_gb"] = 0.000000005  # 5 bytes
		with self.assertRaises(frappe.ValidationError):
			self.maak(bestanden=[self.los])
		frappe.local.conf["openaec_storage_overdracht_max_gb"] = None
		frappe.local.conf["openaec_storage_overdracht_max_bestanden"] = 2
		with self.assertRaises(frappe.ValidationError):
			self.maak(mappen=[self.map], bestanden=[self.los])
		frappe.local.conf.pop("openaec_storage_overdracht_max_bestanden")
		frappe.local.conf["openaec_storage_quotum_gb"] = 0.000000001  # 1 byte: de ruimte zelf zit er al boven
		self.assertEqual(foutcode(self, frappe.ValidationError, self.maak, bestanden=[self.los]), "quotum_pool_vol")
		frappe.local.conf["openaec_storage_quotum_gb"] = 0

	def test_niets_gekopieerd_als_een_item_geweigerd_wordt(self):
		with self.assertRaises(frappe.DoesNotExistError):
			self.maak(bestanden=[self.los, "bestaat-niet"])
		self.assertEqual(objecten("overdrachten/"), [])


class TestBijEenConcept(VanOpslagTest):
	def test_naast_eigen_uploads_in_een_concept(self):
		o = self.nieuwe()
		stuur(o, "lokaal gewijzigd.txt", b"nieuw hier")
		uit = overdracht.voeg_toe_van_opslag(overdracht=o, bestanden=[self.los], mappen=[self.sub])
		self.assertFalse(uit["bezig"])
		self.assertEqual(sorted((b["naam"], b.get("map")) for b in uit["bestanden"]), [("detail.dwg", "2026 fase 2"), ("notitie.txt", None)])
		self.assertTrue(all(b["klaar"] for b in uit["bestanden"]))
		with patch("frappe.sendmail"):
			klaar = overdracht.voltooi(overdracht=o)
		self.assertEqual(
			self.zip_van(klaar),
			{"lokaal gewijzigd.txt": b"nieuw hier", "notitie.txt": b"notitie", "2026 fase 2/detail.dwg": b"DWG detail"},
		)

	def test_dubbele_naam_krijgt_een_nummer(self):
		o = self.nieuwe()
		stuur(o, "notitie.txt", b"andere")
		uit = overdracht.voeg_toe_van_opslag(overdracht=o, bestanden=[self.los])
		self.assertEqual(uit["bestanden"][0]["naam"], "notitie (2).txt")

	def test_alleen_bij_een_eigen_concept(self):
		o = self.nieuwe()
		anders = gebruiker("anders")
		ruimte("Andere ruimte", {anders: "schrijver"})
		frappe.set_user(anders)
		with self.assertRaises(frappe.PermissionError):
			overdracht.voeg_toe_van_opslag(overdracht=o, bestanden=[self.los])
		frappe.set_user(self.anna)
		with patch("frappe.sendmail"):
			stuur(o, "a.txt", b"a")
			overdracht.voltooi(overdracht=o)
		with self.assertRaises(frappe.ValidationError):
			overdracht.voeg_toe_van_opslag(overdracht=o, bestanden=[self.los])


class TestAchtergrond(VanOpslagTest):
	"""Een grote overdracht kopieert de server op de achtergrond; de app volgt hem met `info`."""

	def setUp(self):
		super().setUp()
		self.oud_grens = overdracht.DIRECT_KOPIEREN_BESTANDEN
		overdracht.DIRECT_KOPIEREN_BESTANDEN = 1

	def tearDown(self):
		overdracht.DIRECT_KOPIEREN_BESTANDEN = self.oud_grens
		super().tearDown()

	def test_groot_op_de_achtergrond_en_daarna_actief(self):
		with patch("frappe.enqueue") as taak:
			uitslag = self.maak(mappen=[self.map])
		self.assertEqual((uitslag["status"], uitslag["bezig"], uitslag["aantal_bestanden"]), ("Concept", True, 2))
		info = overdracht.info(overdracht=uitslag["overdracht"])
		self.assertEqual((info["status"], [b["klaar"] for b in info["bestanden"]]), ("Concept", [False, False]))
		# Uploaden of voltooien kan dan nog niet.
		self.assertEqual(
			foutcode(self, frappe.ValidationError, overdracht.voltooi, overdracht=uitslag["overdracht"]), "nog_bezig"
		)
		with self.assertRaises(frappe.ValidationError):
			overdracht.bestand_stand(overdracht=uitslag["overdracht"], bestand=info["bestanden"][0]["bestand"])
		self.assertEqual(taak.call_args.kwargs["overdracht"], uitslag["overdracht"])
		with patch("frappe.sendmail"):
			overdracht.kopieer_uit_opslag(**{k: taak.call_args.kwargs[k] for k in ("overdracht", "voltooien")})
		info = overdracht.info(overdracht=uitslag["overdracht"])
		self.assertEqual(info["status"], "Actief")
		self.assertTrue(info["url"])
		self.assertTrue(all(b["klaar"] for b in info["bestanden"]))

	def test_mislukt_kopiëren_trekt_de_overdracht_in(self):
		with patch("frappe.enqueue") as taak:
			uitslag = self.maak(mappen=[self.map])
		def stuk(bron, doel):
			raise RuntimeError("opslag weg")

		with patch.object(s3, "kopieerder", return_value=stuk), patch("frappe.log_error"):
			overdracht.kopieer_uit_opslag(overdracht=uitslag["overdracht"], voltooien=True)
		self.assertEqual(overdracht.info(overdracht=uitslag["overdracht"])["status"], "Ingetrokken")
		self.assertTrue(taak.called)


class TestGrootBestand(VanOpslagTest):
	def test_groot_bestand_in_delen_gekopieerd(self):
		inhoud = bytes(range(256)) * (24 * MIB // 256)
		groot = upload(self.ruimte, "scan.tif", inhoud)["bestand"]
		with patch.object(s3, "KOPIE_GRENS", 8 * MIB), patch.object(s3, "KOPIE_DEELGROOTTE", 8 * MIB):
			uitslag = self.maak(bestanden=[groot])
		rij = frappe.get_all("Overdrachtbestand", filters={"overdracht": uitslag["overdracht"]}, fields=["name"])[0]
		frappe.set_user("Guest")
		link = overdracht.download(token=uitslag["token"], bestand=rij.name)["url"]
		self.assertEqual(requests.get(link, timeout=60).content, inhoud)
