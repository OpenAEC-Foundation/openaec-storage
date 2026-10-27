import io
import zipfile
from unittest.mock import patch

import boto3
import frappe
import requests
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, getdate, now_datetime

from openaec_storage import api, overdracht
from openaec_storage.tests.hulp import BUCKET, REGIO, NagebootsteS3, gebruiker, met_rol, ruim_op, ruimte, upload
from openaec_storage.tests.test_deelpagina import pagina

MIB = 1024 * 1024


def stuur(overdracht_id: str, naam: str, inhoud: bytes) -> dict:
	"""Eén bestand uploaden zoals de desktop-app het doet: start, delen naar S3, klaar."""
	start = overdracht.bestand_start(overdracht=overdracht_id, naam=naam, grootte=len(inhoud))
	delen = []
	for deel in start["delen"]:
		stuk = inhoud[(deel["nummer"] - 1) * start["deelgrootte"] : deel["nummer"] * start["deelgrootte"]]
		antwoord = requests.put(deel["url"], data=stuk, timeout=30)
		antwoord.raise_for_status()
		delen.append({"nummer": deel["nummer"], "etag": antwoord.headers["ETag"]})
	return overdracht.bestand_klaar(overdracht=overdracht_id, bestand=start["bestand"], delen=delen)


def objecten(voorvoegsel: str = "") -> list[str]:
	antwoord = boto3.client("s3", region_name=REGIO).list_objects_v2(Bucket=BUCKET, Prefix=voorvoegsel)
	return [o["Key"] for o in antwoord.get("Contents", [])]


def opgeslagen_zip(antwoord) -> zipfile.ZipFile:
	return zipfile.ZipFile(io.BytesIO(b"".join(antwoord.response)))


class OverdrachtTest(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		self.wis()
		self.s3 = NagebootsteS3().start()
		self.oud_conf = {
			k: frappe.local.conf.pop(k, None)
			for k in (
				"openaec_storage_overdracht_max_gb",
				"openaec_storage_overdracht_max_dagen",
				"openaec_storage_overdracht_concept_uren",
				"openaec_storage_overdracht_zip_max_gb",
			)
		}
		self.anna = gebruiker("anna")
		self.ruimte = ruimte("Project O", {self.anna: "schrijver"})
		frappe.set_user(self.anna)

	def tearDown(self):
		frappe.set_user("Administrator")
		for k, v in self.oud_conf.items():
			if v is None:
				frappe.local.conf.pop(k, None)
			else:
				frappe.local.conf[k] = v
		frappe.local.openaec_status = None
		self.s3.stop()
		self.wis()
		ruim_op()

	def wis(self):
		frappe.db.delete("Overdrachtbestand")
		frappe.db.delete("Overdracht")

	def nieuwe(self, **extra) -> str:
		return overdracht.maak(naam=extra.pop("naam", "Tekeningen fase 2"), **extra)["overdracht"]

	def actieve(self, bestanden=None, **extra) -> dict:
		o = self.nieuwe(**extra)
		for naam, inhoud in (bestanden or {"plan.pdf": b"%PDF plan", "model.ifc": b"ISO-10303-21"}).items():
			stuur(o, naam, inhoud)
		with patch("frappe.sendmail"):
			return overdracht.voltooi(overdracht=o)


class TestMaken(OverdrachtTest):
	def test_maak_geeft_een_concept_met_standaard_zeven_dagen(self):
		uitslag = overdracht.maak(naam="Tekeningen", bericht="Zie bijlage")
		doc = frappe.get_doc("Overdracht", uitslag["overdracht"])
		self.assertEqual((doc.status, doc.owner, doc.bericht), ("Concept", self.anna, "Zie bijlage"))
		self.assertEqual(getdate(doc.verloopt_op), getdate(add_to_date(now_datetime(), days=7)))
		self.assertEqual(uitslag["status"], "Concept")
		self.assertEqual(uitslag["max_bytes"], 100 * 1000**3)

	def test_gast_mag_geen_overdracht_maken(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			overdracht.maak(naam="x")

	def test_alleen_lezer_mag_geen_overdracht_maken(self):
		lezer = gebruiker("lezer")
		ruimte("Alleen lezen", {lezer: "lezer"})
		frappe.set_user(lezer)
		with self.assertRaises(frappe.PermissionError):
			overdracht.maak(naam="x")

	def test_beheerder_van_de_omgeving_zonder_ruimte_mag_wel(self):
		baas = met_rol(gebruiker("baas"), "OpenAEC Beheerder")
		frappe.set_user(baas)
		self.assertTrue(overdracht.maak(naam="x")["overdracht"])

	def test_vervaltermijn_heeft_een_maximum(self):
		with self.assertRaises(frappe.ValidationError):
			overdracht.maak(naam="x", verloopt_op=str(add_to_date(now_datetime(), days=31)))
		frappe.local.conf["openaec_storage_overdracht_max_dagen"] = 60
		self.assertTrue(overdracht.maak(naam="x", verloopt_op=str(add_to_date(now_datetime(), days=45))))

	def test_vervaldatum_als_datum_geldt_tot_het_eind_van_die_dag(self):
		dag = getdate(add_to_date(now_datetime(), days=3))
		o = overdracht.maak(naam="x", verloopt_op=str(dag))["overdracht"]
		eind = frappe.db.get_value("Overdracht", o, "verloopt_op")
		self.assertEqual((getdate(eind), eind.hour, eind.minute), (dag, 23, 59))

	def test_vervaldatum_in_het_verleden_kan_niet(self):
		with self.assertRaises(frappe.ValidationError):
			overdracht.maak(naam="x", verloopt_op=str(add_to_date(now_datetime(), days=-1)))

	def test_ontvangers_worden_nagekeken_en_begrensd(self):
		o = overdracht.maak(naam="x", ontvangers="jan@voorbeeld.nl, piet@voorbeeld.nl\nJan@voorbeeld.nl")["overdracht"]
		self.assertEqual(frappe.db.get_value("Overdracht", o, "ontvangers"), "jan@voorbeeld.nl\npiet@voorbeeld.nl")
		with self.assertRaises(frappe.ValidationError):
			overdracht.maak(naam="x", ontvangers=["geen-adres"])
		with self.assertRaises(frappe.ValidationError):
			overdracht.maak(naam="x", ontvangers=[f"o{i}@voorbeeld.nl" for i in range(21)])

	def test_grenzen_voor_het_venster(self):
		g = overdracht.grenzen()
		self.assertEqual(
			(g["max_bytes"], g["max_dagen"], g["standaard_dagen"], g["max_ontvangers"], g["concept_uren"]),
			(100 * 1000**3, 30, 7, 20, 24),
		)


class TestUploaden(OverdrachtTest):
	def test_bestanden_staan_onder_een_eigen_voorvoegsel_buiten_de_ruimtes(self):
		o = self.nieuwe()
		stuur(o, "plan.pdf", b"%PDF plan")
		sleutels = objecten()
		self.assertEqual(len(sleutels), 1)
		self.assertTrue(sleutels[0].startswith(f"overdrachten/{o}/"))
		self.assertFalse(frappe.db.exists("Opslagbestand", {"naam": "plan.pdf"}))
		frappe.local.conf["openaec_storage_prefix"] = "klant1"
		try:
			stuur(o, "b.txt", b"b")
			self.assertTrue(any(s.startswith(f"klant1/overdrachten/{o}/") for s in objecten()))
		finally:
			frappe.local.conf.pop("openaec_storage_prefix", None)

	def test_gelijke_naam_krijgt_een_volgnummer(self):
		o = self.nieuwe()
		self.assertEqual(stuur(o, "plan.pdf", b"1")["naam"], "plan.pdf")
		self.assertEqual(stuur(o, "plan.pdf", b"2")["naam"], "plan (2).pdf")

	def test_alleen_de_maker_kan_uploaden(self):
		o = self.nieuwe()
		frappe.set_user(gebruiker("bert"))
		with self.assertRaises(frappe.PermissionError):
			overdracht.bestand_start(overdracht=o, naam="a.txt", grootte=1)

	def test_totale_grootte_heeft_een_maximum(self):
		frappe.local.conf["openaec_storage_overdracht_max_gb"] = 0.00001  # 10 kB
		o = self.nieuwe()
		overdracht.bestand_start(overdracht=o, naam="a.bin", grootte=6000)
		with self.assertRaises(frappe.ValidationError):
			overdracht.bestand_start(overdracht=o, naam="b.bin", grootte=6000)

	def test_quotum_van_de_omgeving_geldt_ook(self):
		frappe.local.conf["openaec_storage_quotum_gb"] = 0.000001  # ~1 kB
		o = self.nieuwe()
		with self.assertRaises(frappe.ValidationError):
			overdracht.bestand_start(overdracht=o, naam="a.bin", grootte=5000)

	def test_bestand_weghalen_voor_het_versturen(self):
		o = self.nieuwe()
		b = stuur(o, "weg.txt", b"weg")["bestand"]
		overdracht.bestand_weg(overdracht=o, bestand=b)
		self.assertFalse(frappe.db.exists("Overdrachtbestand", b))
		self.assertEqual(objecten(), [])

	def test_info_toont_de_bestanden_en_hun_stand(self):
		o = self.nieuwe()
		stuur(o, "a.txt", b"aa")
		overdracht.bestand_start(overdracht=o, naam="b.txt", grootte=3)
		info = overdracht.info(overdracht=o)
		self.assertEqual([(b["naam"], b["klaar"]) for b in info["bestanden"]], [("a.txt", True), ("b.txt", False)])
		self.assertEqual(info["totale_grootte"], 5)


class TestHervatten(OverdrachtTest):
	"""Valt de verbinding weg, dan gaat de upload verder met de delen die nog ontbreken."""

	def test_bestand_stand_zegt_welke_delen_er_al_zijn(self):
		inhoud = b"a" * (8 * MIB) + b"b" * (8 * MIB) + b"c" * 100
		o = self.nieuwe()
		start = overdracht.bestand_start(overdracht=o, naam="groot.bin", grootte=len(inhoud))
		self.assertEqual([d["nummer"] for d in start["delen"]], [1, 2, 3])
		# Alleen deel 1 en 3 komen aan; dan valt de verbinding weg.
		for deel in (start["delen"][0], start["delen"][2]):
			n = deel["nummer"]
			requests.put(deel["url"], data=inhoud[(n - 1) * 8 * MIB : n * 8 * MIB], timeout=30).raise_for_status()

		stand = overdracht.bestand_stand(overdracht=o, bestand=start["bestand"])
		self.assertEqual([d["nummer"] for d in stand["aanwezig"]], [1, 3])
		self.assertEqual(stand["ontbrekend"], [2])
		self.assertEqual([d["nummer"] for d in stand["delen"]], [2])
		self.assertFalse(stand["klaar"])
		with self.assertRaises(frappe.ValidationError):
			overdracht.bestand_klaar(overdracht=o, bestand=start["bestand"])

		requests.put(stand["delen"][0]["url"], data=inhoud[8 * MIB : 16 * MIB], timeout=30).raise_for_status()
		# Zonder lijst van delen: de server kent ze zelf (de app hoeft geen ETags te bewaren).
		klaar = overdracht.bestand_klaar(overdracht=o, bestand=start["bestand"])
		self.assertEqual(klaar["grootte"], len(inhoud))
		self.assertTrue(overdracht.bestand_stand(overdracht=o, bestand=start["bestand"])["klaar"])
		with patch("frappe.sendmail"):
			link = overdracht.voltooi(overdracht=o)
		frappe.set_user("Guest")
		url = overdracht.download(token=link["token"], bestand=start["bestand"])["url"]
		self.assertEqual(requests.get(url, timeout=60).content, inhoud)

	def test_bestand_klaar_is_herhaalbaar(self):
		o = self.nieuwe()
		eerste = stuur(o, "a.txt", b"inhoud")
		self.assertEqual(overdracht.bestand_klaar(overdracht=o, bestand=eerste["bestand"]), eerste)

	def test_voltooien_kan_pas_als_alle_bestanden_er_zijn(self):
		o = self.nieuwe()
		stuur(o, "a.txt", b"a")
		overdracht.bestand_start(overdracht=o, naam="b.txt", grootte=10)
		with self.assertRaises(frappe.ValidationError):
			overdracht.voltooi(overdracht=o)

	def test_lege_overdracht_kan_niet_worden_voltooid(self):
		with self.assertRaises(frappe.ValidationError):
			overdracht.voltooi(overdracht=self.nieuwe())


class TestVoltooien(OverdrachtTest):
	def test_voltooi_geeft_de_link(self):
		link = self.actieve()
		self.assertEqual(link["status"], "Actief")
		self.assertTrue(link["url"].endswith(f"/overdracht/{link['token']}"))
		self.assertEqual((link["aantal_bestanden"], link["totale_grootte"]), (2, len(b"%PDF plan") + len(b"ISO-10303-21")))
		self.assertEqual(link["mail"], [])

	def test_voltooi_mailt_de_ontvangers_met_link_en_bericht(self):
		o = self.nieuwe(ontvangers=["jan@voorbeeld.nl"], bericht="Hierbij de <tekeningen>")
		stuur(o, "plan.pdf", b"%PDF")
		with patch("frappe.sendmail") as sendmail:
			link = overdracht.voltooi(overdracht=o)
		self.assertEqual(link["mail"], ["jan@voorbeeld.nl"])
		kwargs = sendmail.call_args.kwargs
		self.assertEqual(kwargs["recipients"], ["jan@voorbeeld.nl"])
		self.assertIn(link["url"], kwargs["message"])
		self.assertIn("Hierbij de &lt;tekeningen&gt;", kwargs["message"])
		self.assertIn("plan.pdf", kwargs["message"])
		self.assertEqual(kwargs["reply_to"], self.anna)

	def test_wachtwoord_staat_niet_in_de_mail(self):
		o = self.nieuwe(ontvangers=["jan@voorbeeld.nl"], wachtwoord="geheim123")
		stuur(o, "plan.pdf", b"%PDF")
		with patch("frappe.sendmail") as sendmail:
			overdracht.voltooi(overdracht=o)
		self.assertNotIn("geheim123", sendmail.call_args.kwargs["message"])

	def test_na_voltooien_geen_bestanden_meer_toevoegen(self):
		link = self.actieve()
		with self.assertRaises(frappe.ValidationError):
			overdracht.bestand_start(overdracht=link["overdracht"], naam="laat.txt", grootte=1)

	def test_mijn_toont_eigen_overdrachten(self):
		link = self.actieve()
		self.nieuwe(naam="Nog bezig")
		frappe.set_user(gebruiker("bert"))
		self.assertEqual(overdracht.mijn(), [])
		frappe.set_user(self.anna)
		lijst = {r["naam"]: r for r in overdracht.mijn()}
		self.assertEqual(lijst["Tekeningen fase 2"]["status"], "Actief")
		self.assertEqual(lijst["Tekeningen fase 2"]["url"], link["url"])
		self.assertEqual(lijst["Tekeningen fase 2"]["aantal_downloads"], 0)
		self.assertEqual(lijst["Nog bezig"]["status"], "Concept")
		self.assertIsNone(lijst["Nog bezig"]["url"])

	def test_telt_mee_in_het_opslaggebruik_zolang_hij_bestaat(self):
		# Het gebruik van de hele omgeving ziet alleen een lid (systeemgebruiker).
		frappe.db.set_value("User", self.anna, "user_type", "System User")
		upload(self.ruimte, "r.txt", b"12345")
		link = self.actieve({"a.bin": b"123"})
		self.assertEqual(api.quotum()["gebruikt_bytes"], 8)
		self.assertEqual(api.quotum()["overdrachten_bytes"], 3)
		overdracht.trek_in(overdracht=link["overdracht"])
		self.assertEqual(api.quotum()["gebruikt_bytes"], 5)


class TestOntvangen(OverdrachtTest):
	def test_openen_en_downloaden_telt_de_downloads(self):
		link = self.actieve(bericht="Groet")
		frappe.set_user("Guest")
		inhoud = overdracht.openen(token=link["token"])
		self.assertEqual(inhoud["naam"], "Tekeningen fase 2")
		self.assertEqual(inhoud["bericht"], "Groet")
		self.assertEqual(inhoud["afzender"], "anna")
		namen = {b["naam"]: b for b in inhoud["bestanden"]}
		self.assertEqual(namen["plan.pdf"]["grootte"], len(b"%PDF plan"))
		url = overdracht.download(token=link["token"], bestand=namen["plan.pdf"]["bestand"])["url"]
		self.assertEqual(requests.get(url, timeout=30).content, b"%PDF plan")
		self.assertEqual(frappe.db.get_value("Overdracht", link["overdracht"], "aantal_downloads"), 1)

	def test_bestand_van_een_andere_overdracht_niet_downloaden(self):
		een = self.actieve()
		twee = self.actieve({"ander.txt": b"x"})
		vreemd = frappe.db.get_value("Overdrachtbestand", {"overdracht": twee["overdracht"]}, "name")
		frappe.set_user("Guest")
		with self.assertRaises(frappe.DoesNotExistError):
			overdracht.download(token=een["token"], bestand=vreemd)

	def test_wachtwoord(self):
		link = self.actieve(wachtwoord="geheim123")
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			overdracht.openen(token=link["token"])
		with self.assertRaises(frappe.PermissionError):
			overdracht.openen(token=link["token"], wachtwoord="fout")
		self.assertEqual(len(overdracht.openen(token=link["token"], wachtwoord="geheim123")["bestanden"]), 2)

	def test_alles_downloaden_als_zip(self):
		link = self.actieve()
		frappe.set_user("Guest")
		self.assertTrue(overdracht.openen(token=link["token"])["zip"])
		antwoord = overdracht.zip(token=link["token"])
		self.assertEqual(antwoord.mimetype, "application/zip")
		self.assertIn("Tekeningen fase 2.zip", antwoord.headers["Content-Disposition"])
		archief = opgeslagen_zip(antwoord)
		self.assertEqual(archief.read("plan.pdf"), b"%PDF plan")
		self.assertEqual(archief.read("model.ifc"), b"ISO-10303-21")
		self.assertEqual(frappe.db.get_value("Overdracht", link["overdracht"], "aantal_downloads"), 1)

	def test_te_zware_zip_wordt_per_bestand(self):
		frappe.local.conf["openaec_storage_overdracht_zip_max_gb"] = 0.000000001  # 1 byte
		link = self.actieve()
		frappe.set_user("Guest")
		self.assertFalse(overdracht.openen(token=link["token"])["zip"])
		with self.assertRaises(frappe.ValidationError):
			overdracht.zip(token=link["token"])

	def test_ingetrokken_link_werkt_niet_meer_en_bestanden_zijn_weg(self):
		link = self.actieve()
		overdracht.trek_in(overdracht=link["overdracht"])
		self.assertEqual(objecten(f"overdrachten/{link['overdracht']}/"), [])
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			overdracht.openen(token=link["token"])
		self.assertEqual(overdracht.stand(link["token"])[0], "ingetrokken")

	def test_alleen_de_maker_trekt_in(self):
		link = self.actieve()
		frappe.set_user(gebruiker("bert"))
		with self.assertRaises(frappe.PermissionError):
			overdracht.trek_in(overdracht=link["overdracht"])

	def test_concept_heeft_nog_geen_werkende_link(self):
		o = self.nieuwe()
		token = frappe.db.get_value("Overdracht", o, "token")
		self.assertFalse(token)
		self.assertEqual(overdracht.stand(None)[0], "onbekend")


class TestOpruimen(OverdrachtTest):
	def test_verlopen_overdracht_wordt_opgeruimd(self):
		link = self.actieve()
		frappe.db.set_value("Overdracht", link["overdracht"], "verloopt_op", add_to_date(now_datetime(), hours=-1))
		frappe.set_user("Administrator")
		overdracht.opruimen()
		self.assertEqual(frappe.db.get_value("Overdracht", link["overdracht"], "status"), "Verlopen")
		self.assertEqual(objecten(), [])
		self.assertEqual(api.quotum()["gebruikt_bytes"], 0)
		self.assertEqual(overdracht.stand(link["token"])[0], "verlopen")

	def test_onvoltooide_overdracht_blijft_een_dag_staan(self):
		o = self.nieuwe()
		stuur(o, "a.txt", b"a")
		start = overdracht.bestand_start(overdracht=o, naam="half.bin", grootte=10)
		requests.put(start["delen"][0]["url"], data=b"0123456789", timeout=30).raise_for_status()
		frappe.set_user("Administrator")
		overdracht.opruimen()
		self.assertEqual(frappe.db.get_value("Overdracht", o, "status"), "Concept")

		frappe.db.set_value("Overdracht", o, "creation", add_to_date(now_datetime(), hours=-25), update_modified=False)
		overdracht.opruimen()
		self.assertEqual(frappe.db.get_value("Overdracht", o, "status"), "Verlopen")
		self.assertEqual(objecten(), [])
		client = boto3.client("s3", region_name=REGIO)
		self.assertEqual(client.list_multipart_uploads(Bucket=BUCKET).get("Uploads", []), [])

	def test_actieve_overdracht_blijft_staan(self):
		link = self.actieve()
		frappe.set_user("Administrator")
		overdracht.opruimen()
		self.assertEqual(frappe.db.get_value("Overdracht", link["overdracht"], "status"), "Actief")
		self.assertEqual(len(objecten()), 2)


class TestOntvangstpagina(OverdrachtTest):
	def test_geldige_link_toont_de_pagina_in_de_huisstijl(self):
		link = self.actieve()
		frappe.set_user("Guest")
		html = pagina(f"overdracht/{link['token']}")
		self.assertIn('data-stand="geldig"', html)
		self.assertIn("anna", html)
		self.assertIn("openaec_storage.overdracht.", html)
		self.assertIn("Verzonden via", html)
		self.assertIsNone(frappe.local.openaec_status)

	def test_onbekende_link_404(self):
		frappe.set_user("Guest")
		html = pagina("overdracht/bestaat-niet")
		self.assertIn("Deze link werkt niet (meer)", html)
		self.assertEqual(frappe.local.openaec_status, 404)

	def test_verlopen_link_410(self):
		link = self.actieve()
		frappe.db.set_value("Overdracht", link["overdracht"], "verloopt_op", add_to_date(now_datetime(), hours=-1))
		frappe.set_user("Guest")
		html = pagina(f"overdracht/{link['token']}")
		self.assertIn("verlopen", html)
		self.assertEqual(frappe.local.openaec_status, 410)

	def test_ingetrokken_link_410(self):
		link = self.actieve()
		overdracht.trek_in(overdracht=link["overdracht"])
		frappe.set_user("Guest")
		html = pagina(f"overdracht/{link['token']}")
		self.assertIn("ingetrokken", html)
		self.assertEqual(frappe.local.openaec_status, 410)

	def test_token_wordt_ge_escaped(self):
		frappe.set_user("Guest")
		html = pagina('overdracht/x"><script>')
		self.assertNotIn('"><script>', html)
