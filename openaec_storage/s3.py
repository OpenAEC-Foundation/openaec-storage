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
	endpoint = conf.get("openaec_storage_endpoint") or None
	if publiek:
		endpoint = conf.get("openaec_storage_endpoint_publiek") or endpoint
	return boto3.client(
		"s3",
		region_name=conf.get("openaec_storage_region") or "eu-central-1",
		endpoint_url=endpoint,
		config=Config(signature_version="s3v4"),
	)


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


def download_url(sleutel: str, bestandsnaam: str) -> str:
	veilig = bestandsnaam.replace('"', "")
	return _client(publiek=True).generate_presigned_url(
		"get_object",
		Params={
			"Bucket": bucket(),
			"Key": sleutel,
			"ResponseContentDisposition": f'attachment; filename="{veilig}"',
		},
		ExpiresIn=LINK_GELDIG_SECONDEN,
	)
