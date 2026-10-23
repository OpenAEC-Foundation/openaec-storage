"""Hulpfuncties voor de tests: gebruikers, ruimtes en een nagebootste S3 (moto)."""

import boto3
import frappe
import requests
from moto import mock_aws

BUCKET = "openaec-storage-test"
REGIO = "eu-central-1"
DOMEIN = "@storage-test.localhost"


def gebruiker(naam: str) -> str:
	email = naam + DOMEIN
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": naam, "send_welcome_email": 0, "user_type": "System User"}
		).insert(ignore_permissions=True)
	return email


def met_rol(email: str, rol: str) -> str:
	"""Geef een testgebruiker een rol. De beheerrollen komen uit openaec_beheer; zonder die app wordt de rol aangemaakt."""
	if not frappe.db.exists("Role", rol):
		frappe.get_doc({"doctype": "Role", "role_name": rol, "desk_access": 1}).insert(ignore_permissions=True)
	doc = frappe.get_doc("User", email)
	doc.flags.ignore_permissions = True
	doc.add_roles(rol)
	return email


def ruimte(naam: str, leden: dict[str, str]) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "Ruimte",
			"naam": naam,
			"soort": "project",
			"leden": [{"gebruiker": g, "rol": r} for g, r in leden.items()],
		}
	).insert(ignore_permissions=True)
	return doc.name


def ruim_op():
	frappe.set_user("Administrator")
	for doctype in ("Opslagwijziging", "Bestandsversie", "Opslagbestand", "Opslagmap", "Ruimtemapregel", "Ruimte"):
		frappe.db.delete(doctype)
	# Ook de rollen: anders krijgt een testgebruiker met dezelfde naam ze in een volgende test terug.
	frappe.db.delete("Has Role", {"parenttype": "User", "parent": ("like", "%" + DOMEIN)})
	frappe.db.delete("User", {"name": ("like", "%" + DOMEIN)})


# Alle opslaginstellingen van de site. Moto bootst het AWS-adres na: een eigen opslagadres uit de site_config
# (bijv. een S3-compatibele testopslag) zou de verzoeken langs de nabootsing sturen, dus dat gaat tijdens de test uit.
OPSLAGINSTELLINGEN = (
	"openaec_storage_bucket",
	"openaec_storage_region",
	"openaec_storage_quotum_gb",
	"openaec_storage_endpoint",
	"openaec_storage_endpoint_publiek",
	"openaec_storage_prefix",
	"openaec_storage_kms_key",
)


class NagebootsteS3:
	"""Start moto en zet de site-instellingen voor de opslag; herstelt alles bij stop()."""

	def start(self):
		self.mock = mock_aws()
		self.mock.start()
		boto3.client("s3", region_name=REGIO).create_bucket(
			Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGIO}
		)
		self.oud = {k: frappe.local.conf.pop(k, None) for k in OPSLAGINSTELLINGEN}
		frappe.local.conf["openaec_storage_bucket"] = BUCKET
		frappe.local.conf["openaec_storage_region"] = REGIO
		frappe.local.conf["openaec_storage_quotum_gb"] = 0
		return self

	def stop(self):
		for k, v in self.oud.items():
			if v is None:
				frappe.local.conf.pop(k, None)
			else:
				frappe.local.conf[k] = v
		self.mock.stop()


def upload(ruimte_naam: str, naam: str, inhoud: bytes, map_naam=None, basis_versie=None) -> dict:
	"""Volledige upload zoals een client hem doet: start, delen naar S3, klaar."""
	from openaec_storage import api

	start = api.upload_start(
		ruimte=ruimte_naam, naam=naam, grootte=len(inhoud), map=map_naam, basis_versie=basis_versie
	)
	delen = []
	grootte = start["deelgrootte"]
	for deel in start["delen"]:
		stuk = inhoud[(deel["nummer"] - 1) * grootte : deel["nummer"] * grootte]
		antwoord = requests.put(deel["url"], data=stuk, timeout=30)
		antwoord.raise_for_status()
		delen.append({"nummer": deel["nummer"], "etag": antwoord.headers["ETag"]})
	return api.upload_klaar(upload=start["upload"], delen=delen)


def foutcode(test, uitzondering, functie, *args, **kwargs) -> str | None:
	"""Roep `functie` aan, verwacht `uitzondering` en geef de foutcode die in het antwoord zou staan."""
	frappe.local.response.pop("foutcode", None)
	with test.assertRaises(uitzondering):
		functie(*args, **kwargs)
	return frappe.local.response.pop("foutcode", None)
