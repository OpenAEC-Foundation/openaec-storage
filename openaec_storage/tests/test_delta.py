import hashlib

import frappe
import requests
from frappe.tests import IntegrationTestCase

from openaec_storage import api, s3
from openaec_storage.tests.hulp import NagebootsteS3, gebruiker, ruim_op, ruimte

MIB = 1024 * 1024
BLOK = 8 * MIB


def hashes(inhoud: bytes, blok: int = BLOK) -> list[str]:
	return [hashlib.sha256(inhoud[i : i + blok]).hexdigest() for i in range(0, max(len(inhoud), 1), blok)]


def delta_upload(ruimte_naam, naam, inhoud, basis_versie=None):
	start = api.upload_start(
		ruimte=ruimte_naam, naam=naam, grootte=len(inhoud), basis_versie=basis_versie, blokhashes=hashes(inhoud)
	)
	delen = []
	for deel in start["delen"]:
		stuk = inhoud[(deel["nummer"] - 1) * start["deelgrootte"] : deel["nummer"] * start["deelgrootte"]]
		antwoord = requests.put(deel["url"], data=stuk, timeout=60)
		antwoord.raise_for_status()
		delen.append({"nummer": deel["nummer"], "etag": antwoord.headers["ETag"]})
	return start, api.upload_klaar(upload=start["upload"], delen=delen)


class TestDelta(IntegrationTestCase):
	def setUp(self):
		ruim_op()
		self.s3 = NagebootsteS3().start()
		self.schrijver = gebruiker("delta")
		self.ruimte = ruimte("Delta", {self.schrijver: "schrijver"})
		frappe.set_user(self.schrijver)
		self.a, self.b, self.c = b"A" * BLOK, b"B" * BLOK, b"staart" * 20
		self.eerste_start, self.eerste = delta_upload(self.ruimte, "wolk.e57", self.a + self.b + self.c)

	def tearDown(self):
		self.s3.stop()
		ruim_op()

	def test_eerste_upload_stuurt_alle_blokken(self):
		self.assertEqual([d["nummer"] for d in self.eerste_start["delen"]], [1, 2, 3])
		self.assertEqual(self.eerste_start["hergebruik"], [])

	def test_alleen_gewijzigd_blok_wordt_geupload(self):
		nieuw = self.a + b"X" * BLOK + self.c
		start, resultaat = delta_upload(self.ruimte, "wolk.e57", nieuw, basis_versie=1)
		self.assertEqual([d["nummer"] for d in start["delen"]], [2])
		self.assertEqual(start["hergebruik"], [1, 3])
		self.assertEqual(resultaat["versie"], 2)
		url = api.download(bestand=resultaat["bestand"])["url"]
		self.assertEqual(requests.get(url, timeout=60).content, nieuw)

	def test_zonder_basisversie_geen_hergebruik(self):
		start, resultaat = delta_upload(self.ruimte, "ander.e57", self.a + self.b)
		self.assertEqual(start["hergebruik"], [])

	def test_blokken_geeft_hashes_van_de_versie(self):
		blokken = api.blokken(bestand=self.eerste["bestand"])
		self.assertEqual(blokken["blokgrootte"], BLOK)
		self.assertEqual(blokken["hashes"], hashes(self.a + self.b + self.c))

	def test_blokgrootte_groeit_boven_80_gib(self):
		self.assertEqual(s3.blokgrootte(10 * 1024 * MIB), BLOK)
		groot = 200 * 1024 * MIB
		blok = s3.blokgrootte(groot)
		self.assertGreater(blok, BLOK)
		self.assertEqual(blok % MIB, 0)
		self.assertLessEqual(-(-groot // blok), 9000)
