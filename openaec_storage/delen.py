"""Deellinks zonder account (ontwerp §3): openen en downloaden met alleen het token (en eventueel een
wachtwoord). Meewerken vraagt een (gratis) account met een deling als gebruiker.

Links naar een gearchiveerde ruimte werken niet, ook niet om te lezen of te downloaden: archiveren betekent dat de
ruimte af is, en wie dan nog bestanden nodig heeft, vraagt het de beheerder. De links blijven bestaan en werken weer
als de ruimte wordt teruggezet.

Een uploadlink (`rechten="uploaden"`, alleen voor een map) is het omgekeerde: bestanden opvragen. De ontvanger
ziet de inhoud van de map niet en kan niets downloaden, alleen bestanden aanleveren. Een aangeleverd bestand
overschrijft nooit iets: bij een gelijke naam komt er een volgnummer bij. De maker van de link krijgt een melding.
"""

import json
import math
import os

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt, now_datetime

from openaec_storage import mapsjablonen, s3
from openaec_storage.rechten import is_gearchiveerd, voorouders, weiger_als_gearchiveerd

# Bestanden die de deelpagina in de browser kan tonen (geen SVG: dat is een document met scripts).
VOORBEELD = {
	".pdf": "application/pdf",
	".png": "image/png",
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".gif": "image/gif",
	".webp": "image/webp",
}

UPLOAD_SLEUTEL = "openaec_storage:linkupload:"
UPLOAD_GELDIG_SECONDEN = 24 * 3600
STANDAARD_MAX_GB = 5


def _deling(token: str, wachtwoord: str | None):
	from frappe.utils.password import passlibctx

	rij = frappe.db.get_value(
		"Opslagdeling",
		{"token": token or "", "soort": "link"},
		["name", "ruimte", "doel_doctype", "doel", "rechten", "wachtwoord_hash", "verloopt", "ingetrokken", "owner"],
		as_dict=True,
	)
	if not rij or rij.ingetrokken or (rij.verloopt and rij.verloopt < now_datetime()):
		frappe.throw(_("Deze link is niet (meer) geldig"), frappe.PermissionError)
	# Zolang de ruimte gearchiveerd is, werkt geen enkele link erheen (ook niet om te lezen); na terugzetten weer wel.
	weiger_als_gearchiveerd(rij.ruimte)
	if rij.wachtwoord_hash and not (wachtwoord and passlibctx.verify(wachtwoord, rij.wachtwoord_hash)):
		frappe.throw(_("Wachtwoord vereist of onjuist"), frappe.PermissionError)
	return rij


def stand(token: str | None) -> tuple[str, dict | None]:
	"""Wat de deelpagina moet tonen, zonder iets prijs te geven: `geldig`, `wachtwoord` (eerst een wachtwoord),
	`onbekend` (bestaat niet of ingetrokken), `verlopen`, `gearchiveerd` (de ruimte staat in het archief) of `weg`
	(doel verwijderd), plus de deling."""
	rij = (
		frappe.db.get_value(
			"Opslagdeling",
			{"token": token, "soort": "link"},
			["name", "ruimte", "doel_doctype", "doel", "rechten", "wachtwoord_hash", "verloopt", "ingetrokken", "owner"],
			as_dict=True,
		)
		if token
		else None
	)
	if not rij or rij.ingetrokken:
		return "onbekend", None
	if rij.verloopt and rij.verloopt < now_datetime():
		return "verlopen", rij
	if is_gearchiveerd(rij.ruimte):
		return "gearchiveerd", rij
	if frappe.db.get_value(rij.doel_doctype, rij.doel, "verwijderd_op") or not frappe.db.exists(rij.doel_doctype, rij.doel):
		return "weg", rij
	return ("wachtwoord" if rij.wachtwoord_hash else "geldig"), rij


def na_verzoek(response=None, request=None) -> None:
	"""Hook `after_request`: de statuscode die de deelpagina koos (404/410 bij een ongeldige link)."""
	status = getattr(frappe.local, "openaec_status", None)
	if status and response is not None:
		response.status_code = status


def _voorbeeld(naam: str) -> bool:
	return os.path.splitext(naam or "")[1].lower() in VOORBEELD


def _bestand_info(naam: str) -> dict:
	return frappe.db.get_value(
		"Opslagbestand", naam, ["name", "naam", "grootte", "huidige_versie", "map", "verwijderd_op"], as_dict=True
	)


def _binnen_gedeelde_map(deling, map: str | None) -> bool:
	return deling.doel_doctype == "Opslagmap" and deling.doel in voorouders(map)


@frappe.whitelist(allow_guest=True)
def openen(token: str, wachtwoord: str | None = None, map: str | None = None) -> dict:
	"""Wat er achter de link zit: één bestand, of de inhoud van (een submap van) de gedeelde map."""
	deling = _deling(token, wachtwoord)
	if deling.doel_doctype == "Opslagbestand":
		bestand = _bestand_info(deling.doel)
		if not bestand or bestand.verwijderd_op:
			frappe.throw(_("Dit bestand is niet meer beschikbaar"), frappe.DoesNotExistError)
		gewijzigd = frappe.db.get_value("Opslagbestand", bestand.name, "modified")
		return {
			"bestand": {
				"naam": bestand.naam,
				"grootte": bestand.grootte,
				"gewijzigd": gewijzigd,
				"voorbeeld": _voorbeeld(bestand.naam),
			}
		}
	if deling.rechten == "uploaden":
		return {
			"uploaden": True,
			"map": frappe.db.get_value("Opslagmap", deling.doel, "naam"),
			"max_grootte": _max_grootte(),
		}
	map = map or deling.doel
	if not _binnen_gedeelde_map(deling, map):
		frappe.throw(_("Deze map valt buiten de deling"), frappe.PermissionError)
	return {
		"map": frappe.db.get_value("Opslagmap", map, "naam"),
		"mappen": frappe.get_all(
			"Opslagmap",
			filters={"bovenliggende": map, "verwijderd_op": ("is", "not set")},
			fields=["name", "naam"],
			order_by="naam asc",
			ignore_permissions=True,
		),
		"bestanden": [
			{**b, "voorbeeld": _voorbeeld(b.naam)}
			for b in frappe.get_all(
				"Opslagbestand",
				filters={"map": map, "verwijderd_op": ("is", "not set")},
				fields=["name", "naam", "grootte", "modified as gewijzigd"],
				order_by="naam asc",
				ignore_permissions=True,
			)
		],
	}


@frappe.whitelist(allow_guest=True)
def download(token: str, wachtwoord: str | None = None, bestand: str | None = None, voorbeeld: int = 0) -> dict:
	"""Tijdelijke downloadlink voor het gedeelde bestand, of voor een bestand binnen de gedeelde map. Met `voorbeeld`
	(alleen PDF en afbeeldingen) een link die de browser toont in plaats van downloadt."""
	deling = _deling(token, wachtwoord)
	if deling.rechten == "uploaden":
		frappe.throw(_("Via deze link kun je alleen bestanden aanleveren"), frappe.PermissionError)
	naam = deling.doel if deling.doel_doctype == "Opslagbestand" else bestand
	info = _bestand_info(naam) if naam else None
	if not info or info.verwijderd_op:
		frappe.throw(_("Bestand niet gevonden"), frappe.DoesNotExistError)
	if deling.doel_doctype == "Opslagmap" and not _binnen_gedeelde_map(deling, info.map):
		frappe.throw(_("Dit bestand valt buiten de deling"), frappe.PermissionError)
	sleutel = frappe.db.get_value(
		"Bestandsversie", {"bestand": info.name, "volgnummer": info.huidige_versie}, "s3_sleutel"
	)
	inhoudstype = VOORBEELD.get(os.path.splitext(info.naam)[1].lower()) if cint(voorbeeld) else None
	return {"url": s3.download_url(sleutel, info.naam, inhoudstype), "geldig_seconden": s3.LINK_GELDIG_SECONDEN}


# --- bestanden opvragen (uploadlink) -----------------------------------------------------------------


def _max_grootte() -> int:
	"""Grootste bestand dat via een uploadlink mag (openaec_storage_link_max_gb, standaard 5 GB)."""
	return int(flt(frappe.conf.get("openaec_storage_link_max_gb") or STANDAARD_MAX_GB) * 1024**3)


def _uploaddeling(token: str, wachtwoord: str | None):
	deling = _deling(token, wachtwoord)
	if deling.rechten != "uploaden" or deling.doel_doctype != "Opslagmap":
		frappe.throw(_("Via deze link kun je geen bestanden aanleveren"), frappe.PermissionError)
	if frappe.db.get_value("Opslagmap", deling.doel, "verwijderd_op"):
		frappe.throw(_("Deze map is niet meer beschikbaar"), frappe.DoesNotExistError)
	return deling


def _vrije_naam(ruimte: str, map: str, naam: str) -> str:
	"""`naam`, of `naam (2)`, `naam (3)`, ... als er in de map al een bestand met die naam staat."""
	from openaec_storage.api import _actief_bestand

	stam, ext = os.path.splitext(naam)
	kandidaat, teller = naam, 2
	while _actief_bestand(ruimte, map, kandidaat):
		kandidaat = f"{stam} ({teller}){ext}"
		teller += 1
	return kandidaat


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=300, seconds=3600)
def upload_start(token: str, naam: str, grootte: int, wachtwoord: str | None = None) -> dict:
	"""Begin het aanleveren van één bestand: een upload-token en per deel een ondertekende URL."""
	from openaec_storage.api import _controleer_naam, _controleer_quotum

	deling = _uploaddeling(token, wachtwoord)
	naam = _controleer_naam(naam)
	mapsjablonen.controleer_upload(deling.ruimte, deling.doel, naam)
	grootte = cint(grootte)
	if grootte < 0:
		frappe.throw(_("Ongeldige grootte"))
	if grootte > _max_grootte():
		frappe.throw(_("Dit bestand is groter dan via deze link is toegestaan"))
	_controleer_quotum(grootte, deling.ruimte)

	blok = s3.blokgrootte(grootte)
	aantal = max(1, math.ceil(grootte / blok))
	sleutel = s3.nieuwe_sleutel()
	upload_id = s3.start_multipart(sleutel)
	upload = frappe.generate_hash(length=32)
	frappe.cache.set_value(
		UPLOAD_SLEUTEL + upload,
		{
			"deling": deling.name,
			"naam": naam,
			"grootte": grootte,
			"sleutel": sleutel,
			"upload_id": upload_id,
			"blokgrootte": blok,
		},
		expires_in_sec=UPLOAD_GELDIG_SECONDEN,
	)
	return {
		"upload": upload,
		"deelgrootte": blok,
		"delen": [{"nummer": n, "url": s3.deel_url(sleutel, upload_id, n)} for n in range(1, aantal + 1)],
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=300, seconds=3600)
def upload_klaar(token: str, upload: str, delen, wachtwoord: str | None = None, afzender: str | None = None) -> dict:
	"""Rond het aanleveren af: een nieuw bestand in de gedeelde map (nooit een overschrijving)."""
	from openaec_storage.api import _log

	deling = _uploaddeling(token, wachtwoord)
	info = frappe.cache.get_value(UPLOAD_SLEUTEL + (upload or ""))
	if not info or info["deling"] != deling.name:
		frappe.throw(_("Upload onbekend of verlopen"), frappe.PermissionError)
	mapsjablonen.controleer_upload(deling.ruimte, deling.doel, info["naam"])
	if isinstance(delen, str):
		delen = json.loads(delen)
	s3.voltooi(info["sleutel"], info["upload_id"], delen)
	if s3.grootte(info["sleutel"]) != info["grootte"]:
		frappe.throw(_("Ontvangen grootte wijkt af van de aangekondigde grootte"))

	map = deling.doel
	# Twee gelijktijdige uploads met dezelfde naam: de tweede ziet de eerste en krijgt een volgnummer.
	frappe.db.sql("select name from `tabOpslagmap` where name = %s for update", map)
	naam = _vrije_naam(deling.ruimte, map, info["naam"])
	doc = frappe.get_doc(
		{
			"doctype": "Opslagbestand",
			"ruimte": deling.ruimte,
			"map": map,
			"naam": naam,
			"huidige_versie": 1,
			"grootte": info["grootte"],
		}
	).insert(ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "Bestandsversie",
			"bestand": doc.name,
			"ruimte": deling.ruimte,
			"volgnummer": 1,
			"s3_sleutel": info["sleutel"],
			"grootte": info["grootte"],
			"bron": "link",
			"maker": "Guest",
			"blokgrootte": info["blokgrootte"],
		}
	).insert(ignore_permissions=True)
	_log(deling.ruimte, "nieuw", "Opslagbestand", doc.name, naam)
	frappe.cache.delete_value(UPLOAD_SLEUTEL + upload)
	_meld_maker(deling, naam, (afzender or "").strip()[:140])
	return {"naam": naam}


def _meld_maker(deling, naam: str, afzender: str) -> None:
	"""Melding (bel in de desk) aan wie de uploadlink maakte; een mislukte melding laat de upload staan."""
	if not deling.owner or deling.owner in ("Guest", "Administrator"):
		return
	map_naam = frappe.db.get_value("Opslagmap", deling.doel, "naam")
	onderwerp = (
		_("{0} heeft {1} aangeleverd in {2}").format(afzender, naam, map_naam)
		if afzender
		else _("{0} is aangeleverd in {1}").format(naam, map_naam)
	)
	try:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": deling.owner,
				"type": "Alert",
				"subject": frappe.utils.escape_html(onderwerp),
				"document_type": "Opslagmap",
				"document_name": deling.doel,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Melding over een aangeleverd bestand niet gemaakt")
