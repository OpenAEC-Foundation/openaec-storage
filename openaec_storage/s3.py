"""Koppeling met S3. Frappe raakt de bytes niet: clients uploaden en downloaden met ondertekende links."""

import boto3
import frappe
from botocore.config import Config
from frappe import _

DEELGROOTTE = 8 * 1024 * 1024  # vaste blokgrootte (ontwerp §5); ook de partgrootte van multipart-uploads
LINK_GELDIG_SECONDEN = 900


def _client(publiek: bool = False):
	"""S3-client. `publiek`: voor ondertekende links die de browser of sync-client gebruikt; die kunnen een
	ander adres nodig hebben dan de server zelf (openaec_storage_endpoint_publiek). Op AWS zijn ze gelijk."""
	conf = frappe.conf
	regio = conf.get("openaec_storage_region") or "eu-central-1"
	endpoint = conf.get("openaec_storage_endpoint") or None
	if publiek:
		endpoint = conf.get("openaec_storage_endpoint_publiek") or endpoint
	if endpoint:
		# Eigen opslagadres (S3-compatibel): pad-adressering, want <bucket>.<host> bestaat daar niet.
		adressering = "path"
	else:
		# AWS: altijd het regionale adres. Zonder dat tekent boto3 links voor <bucket>.s3.amazonaws.com; S3 stuurt
		# die door naar de regio en een client die de doorverwijzing volgt, breekt daarmee de handtekening.
		endpoint, adressering = f"https://s3.{regio}.amazonaws.com", "virtual"
	return boto3.client(
		"s3",
		region_name=regio,
		endpoint_url=endpoint,
		config=Config(signature_version="s3v4", s3={"addressing_style": adressering}),
	)


def _met_prefix(sleutel: str) -> str:
	prefix = (frappe.conf.get("openaec_storage_prefix") or "").strip("/")
	return f"{prefix}/{sleutel}" if prefix else sleutel


def nieuwe_sleutel() -> str:
	"""S3-sleutel voor een nieuwe versie. Met `openaec_storage_prefix` (bijv. de host van de klantsite) kunnen
	meerdere klanten één bucket delen; de volledige sleutel staat bij de versie, dus oude versies blijven leesbaar."""
	return _met_prefix("v/" + frappe.generate_hash(length=32))


def overdracht_map(overdracht: str) -> str:
	"""Voorvoegsel van alle objecten van één overdracht: buiten de ruimtes, in één keer op te ruimen."""
	return _met_prefix(f"overdrachten/{overdracht}/")


def overdracht_sleutel(overdracht: str) -> str:
	return overdracht_map(overdracht) + frappe.generate_hash(length=32)


def bucket() -> str:
	naam = frappe.conf.get("openaec_storage_bucket")
	if not naam:
		frappe.throw(_("Opslag is niet ingesteld (openaec_storage_bucket ontbreekt)"))
	return naam


def _versleuteling() -> dict:
	sleutel = frappe.conf.get("openaec_storage_kms_key")
	if sleutel:
		return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": sleutel}
	return {}


def start_multipart(sleutel: str) -> str:
	antwoord = _client().create_multipart_upload(Bucket=bucket(), Key=sleutel, **_versleuteling())
	return antwoord["UploadId"]


def deel_url(sleutel: str, upload_id: str, nummer: int) -> str:
	return _client(publiek=True).generate_presigned_url(
		"upload_part",
		Params={"Bucket": bucket(), "Key": sleutel, "UploadId": upload_id, "PartNumber": nummer},
		ExpiresIn=LINK_GELDIG_SECONDEN,
	)


def voltooi(sleutel: str, upload_id: str, delen: list[dict]) -> None:
	_client().complete_multipart_upload(
		Bucket=bucket(),
		Key=sleutel,
		UploadId=upload_id,
		MultipartUpload={
			"Parts": [{"PartNumber": int(d["nummer"]), "ETag": d["etag"]} for d in sorted(delen, key=lambda d: int(d["nummer"]))]
		},
	)


def afbreken(sleutel: str, upload_id: str) -> None:
	_client().abort_multipart_upload(Bucket=bucket(), Key=sleutel, UploadId=upload_id)


def grootte(sleutel: str) -> int:
	return int(_client().head_object(Bucket=bucket(), Key=sleutel)["ContentLength"])


def download_url(sleutel: str, bestandsnaam: str, inhoudstype: str | None = None) -> str:
	"""Ondertekende downloadlink. Met `inhoudstype` toont de browser het bestand (inline, voor een voorbeeld van een
	PDF of afbeelding); zonder wordt het een download."""
	veilig = bestandsnaam.replace('"', "")
	params = {
		"Bucket": bucket(),
		"Key": sleutel,
		"ResponseContentDisposition": f'{"inline" if inhoudstype else "attachment"}; filename="{veilig}"',
	}
	if inhoudstype:
		params["ResponseContentType"] = inhoudstype
	return _client(publiek=True).generate_presigned_url("get_object", Params=params, ExpiresIn=LINK_GELDIG_SECONDEN)


MAX_DELEN = 9000  # onder de S3-grens van 10 000 parts, met ruimte voor groei
MIB = 1024 * 1024


def blokgrootte(grootte: int) -> int:
	"""Blokgrootte voor een bestand (ontwerp §5): 8 MiB, of groter boven 80 GiB (max. 9000 blokken)."""
	if grootte <= DEELGROOTTE * MAX_DELEN:
		return DEELGROOTTE
	nodig = -(-grootte // MAX_DELEN)
	return -(-nodig // MIB) * MIB


def kopieer_deel(sleutel: str, upload_id: str, nummer: int, bron_sleutel: str, begin: int, eind: int) -> str:
	"""Kopieer een blok uit een eerdere versie server-side in de nieuwe multipart-upload; geeft de ETag."""
	antwoord = _client().upload_part_copy(
		Bucket=bucket(),
		Key=sleutel,
		UploadId=upload_id,
		PartNumber=nummer,
		CopySource={"Bucket": bucket(), "Key": bron_sleutel},
		CopySourceRange=f"bytes={begin}-{eind}",
	)
	return antwoord["CopyPartResult"]["ETag"]


# --- overdrachten: hervatten, opruimen en als zip lezen ----------------------------------------------


def aanwezige_delen(sleutel: str, upload_id: str) -> list[dict]:
	"""De delen die S3 al van een lopende multipart-upload heeft: nummer, grootte en ETag (oplopend)."""
	client, uit, vanaf = _client(), [], 0
	while True:
		antwoord = client.list_parts(Bucket=bucket(), Key=sleutel, UploadId=upload_id, PartNumberMarker=vanaf)
		uit += [{"nummer": d["PartNumber"], "grootte": d["Size"], "etag": d["ETag"]} for d in antwoord.get("Parts", [])]
		if not antwoord.get("IsTruncated"):
			return sorted(uit, key=lambda d: d["nummer"])
		vanaf = antwoord["NextPartNumberMarker"]


def verwijder_map(voorvoegsel: str) -> None:
	"""Alle objecten en onvoltooide multipart-uploads onder een voorvoegsel weg (opruimen van een overdracht)."""
	client, naam = _client(), bucket()
	for pagina in client.get_paginator("list_multipart_uploads").paginate(Bucket=naam, Prefix=voorvoegsel):
		for upload in pagina.get("Uploads", []):
			client.abort_multipart_upload(Bucket=naam, Key=upload["Key"], UploadId=upload["UploadId"])
	for pagina in client.get_paginator("list_objects_v2").paginate(Bucket=naam, Prefix=voorvoegsel):
		sleutels = [{"Key": o["Key"]} for o in pagina.get("Contents", [])]
		if sleutels:
			client.delete_objects(Bucket=naam, Delete={"Objects": sleutels, "Quiet": True})


def verwijder(sleutel: str) -> None:
	_client().delete_object(Bucket=bucket(), Key=sleutel)


def lezer():
	"""Functie die een object in stukken leest, voor gebruik buiten het verzoek (een gestreamde zip): client en
	bucket liggen hier al vast, want de stroom loopt nog als Frappe het verzoek al heeft afgesloten."""
	client, naam = _client(), bucket()

	def lees(sleutel: str, stukgrootte: int = 1024 * 1024):
		yield from client.get_object(Bucket=naam, Key=sleutel)["Body"].iter_chunks(stukgrootte)

	return lees
