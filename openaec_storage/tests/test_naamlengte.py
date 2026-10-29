"""Lange bestandsnamen: elk naamveld van bestanden en mappen houdt de 255 tekens die `namen.MAX_NAAM` toestaat.

Frappe geeft een Data-veld standaard 140 tekens; een normbestand of documenttitel is vaak langer. Dan weigerde het
opslaan met "zal afgekapt worden", terwijl de naam volgens de opslag zelf geldig was.
"""

import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage.namen import MAX_NAAM

NAAMVELDEN = ("Opslagbestand", "Opslagmap", "Opslagwijziging", "Overdrachtbestand")


class TestNaamlengte(IntegrationTestCase):
	def test_naamvelden_houden_de_maximale_naam(self):
		for doctype in NAAMVELDEN:
			with self.subTest(doctype=doctype):
				veld = frappe.get_meta(doctype).get_field("naam")
				self.assertGreaterEqual(veld.length or 140, MAX_NAAM)

	def test_kolom_in_de_database_is_lang_genoeg(self):
		for doctype in NAAMVELDEN:
			with self.subTest(doctype=doctype):
				lengte = frappe.db.sql(
					"""select character_maximum_length from information_schema.columns
					where table_schema = database() and table_name = %s and column_name = 'naam'""",
					(f"tab{doctype}",),
				)[0][0]
				self.assertGreaterEqual(lengte, MAX_NAAM)
