"""Deellinks zonder account (ontwerp §3): openen en downloaden met alleen het token (en eventueel een
wachtwoord). Alleen lezen; meewerken vraagt een (gratis) account met een deling als gebruiker.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from openaec_storage import s3
from openaec_storage.rechten import voorouders


def _deling(token: str, wachtwoord: str | None):
	from frappe.utils.password import passlibctx

	rij = frappe.db.get_value(
		"Opslagdeling",
		{"token": token or "", "soort": "link"},
		["name", "ruimte", "doel_doctype", "doel", "wachtwoord_hash", "verloopt", "ingetrokken"],
		as_dict=True,
	)
	if not rij or rij.ingetrokken or (rij.verloopt and rij.verloopt < now_datetime()):
		frappe.throw(_("Deze link is niet (meer) geldig"), frappe.PermissionError)
	if rij.wachtwoord_hash and not (wachtwoord and passlibctx.verify(wachtwoord, rij.wachtwoord_hash)):
		frappe.throw(_("Wachtwoord vereist of onjuist"), frappe.PermissionError)
	return rij


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
		return {"bestand": {"naam": bestand.naam, "grootte": bestand.grootte}}
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
		"bestanden": frappe.get_all(
			"Opslagbestand",
			filters={"map": map, "verwijderd_op": ("is", "not set")},
			fields=["name", "naam", "grootte"],
			order_by="naam asc",
			ignore_permissions=True,
		),
	}


@frappe.whitelist(allow_guest=True)
def download(token: str, wachtwoord: str | None = None, bestand: str | None = None) -> dict:
	"""Tijdelijke downloadlink voor het gedeelde bestand, of voor een bestand binnen de gedeelde map."""
	deling = _deling(token, wachtwoord)
	naam = deling.doel if deling.doel_doctype == "Opslagbestand" else bestand
	info = _bestand_info(naam) if naam else None
	if not info or info.verwijderd_op:
		frappe.throw(_("Bestand niet gevonden"), frappe.DoesNotExistError)
	if deling.doel_doctype == "Opslagmap" and not _binnen_gedeelde_map(deling, info.map):
		frappe.throw(_("Dit bestand valt buiten de deling"), frappe.PermissionError)
	sleutel = frappe.db.get_value(
		"Bestandsversie", {"bestand": info.name, "volgnummer": info.huidige_versie}, "s3_sleutel"
	)
	return {"url": s3.download_url(sleutel, info.naam), "geldig_seconden": s3.LINK_GELDIG_SECONDEN}
