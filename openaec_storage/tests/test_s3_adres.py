import os
from unittest.mock import patch
from urllib.parse import urlparse

import frappe
from frappe.tests import IntegrationTestCase

from openaec_storage import s3


class TestS3Adres(IntegrationTestCase):
	"""Ondertekende links moeten meteen naar het juiste adres wijzen: een doorverwijzing breekt de handtekening."""

	def setUp(self):
		# Ondertekenen vraagt sleutels; er gaat niets over de lijn.
		self.sleutels = patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AKIATEST", "AWS_SECRET_ACCESS_KEY": "geheim"})
		self.sleutels.start()
		self.oud = {k: frappe.local.conf.get(k) for k in ("openaec_storage_endpoint", "openaec_storage_endpoint_publiek", "openaec_storage_region", "openaec_storage_bucket")}
		frappe.local.conf.update({"openaec_storage_bucket": "mijn-bucket", "openaec_storage_region": "eu-central-1"})
		frappe.local.conf.pop("openaec_storage_endpoint", None)
		frappe.local.conf.pop("openaec_storage_endpoint_publiek", None)

	def tearDown(self):
		self.sleutels.stop()
		for k, v in self.oud.items():
			if v is None:
				frappe.local.conf.pop(k, None)
			else:
				frappe.local.conf[k] = v

	def test_aws_gebruikt_het_regionale_adres_met_de_bucket_in_de_hostnaam(self):
		url = s3._client(publiek=True).generate_presigned_url("get_object", Params={"Bucket": "mijn-bucket", "Key": "a/b"})
		self.assertEqual(urlparse(url).hostname, "mijn-bucket.s3.eu-central-1.amazonaws.com")

	def test_eigen_opslagadres_gebruikt_pad_adressering(self):
		frappe.local.conf["openaec_storage_endpoint_publiek"] = "http://opslag.test:9000"
		url = s3._client(publiek=True).generate_presigned_url("get_object", Params={"Bucket": "mijn-bucket", "Key": "a/b"})
		delen = urlparse(url)
		self.assertEqual((delen.hostname, delen.path), ("opslag.test", "/mijn-bucket/a/b"))
