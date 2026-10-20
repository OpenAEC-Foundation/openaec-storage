"""API van OpenAEC Storage (fase 4a): bladeren, uploaden in delen naar S3, downloaden, versies,
prullenbak, wijzigingenlijst en quotum. Rechten volgen uit de ruimte (zie rechten.py).

Conflictregel (ontwerp §6): bij `upload_klaar` wint een upload alleen als `basis_versie` gelijk is aan de
huidige versie; anders wordt het een conflictkopie naast het origineel. Er gaat nooit iets verloren.
"""

import json
import math
import os

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, now_datetime

from openaec_storage import s3
from openaec_storage.rechten import mag, vereis, vereis_op_plek

UPLOAD_SLEUTEL = "openaec_storage:upload:"
UPLOAD_GELDIG_SECONDEN = 7 * 24 * 3600
BRONNEN = ("web", "sync", "webdav", "api")
SLOT_UREN = 8


# --- hulpfuncties ------------------------------------------------------------------------------------


def _controleer_naam(naam: str) -> str:
	naam = (naam or "").strip()
	if not naam or naam in (".", "..") or "/" in naam or "\\" in naam:
		frappe.throw(_("Ongeldige naam"))
	return naam


def _controleer_map(ruimte: str, map: str | None) -> str | None:
	if not map:
		return None
	if frappe.db.get_value("Opslagmap", map, "ruimte") != ruimte:
		frappe.throw(_("Map hoort niet bij deze ruimte"))
	return map


def _actief_bestand(ruimte: str, map: str | None, naam: str):
	return frappe.db.get_value(
		"Opslagbestand",
		{"ruimte": ruimte, "map": map or ("is", "not set"), "naam": naam, "verwijderd_op": ("is", "not set")},
		["name", "huidige_versie"],
		as_dict=True,
	)


def _conflictnaam(ruimte: str, map: str | None, naam: str) -> str:
	stam, ext = os.path.splitext(naam)
	wie = frappe.db.get_value("User", frappe.session.user, "first_name") or frappe.session.user
	basis = "{} (conflict {} {}){}".format(stam, wie, now_datetime().strftime("%Y-%m-%d %H%M"), ext)
	kandidaat, teller = basis, 2
	while _actief_bestand(ruimte, map, kandidaat):
		kandidaat = "{} (conflict {} {} {}){}".format(stam, wie, now_datetime().strftime("%Y-%m-%d %H%M"), teller, ext)
		teller += 1
	return kandidaat


def _log(ruimte: str, soort: str, doctype: str, doel: str, naam: str | None = None) -> None:
	frappe.get_doc(
		{"doctype": "Opslagwijziging", "ruimte": ruimte, "soort": soort, "doel_doctype": doctype, "doel": doel, "naam": naam}
	).insert(ignore_permissions=True)


def _gebruikt_bytes() -> int:
	return cint(frappe.db.sql("select coalesce(sum(grootte), 0) from `tabBestandsversie`")[0][0])


def _limiet_bytes() -> int:
	return int(flt(frappe.conf.get("openaec_storage_quotum_gb")) * 1_000_000_000)


def _controleer_quotum(extra: int) -> None:
	limiet = _limiet_bytes()
	if limiet and _gebruikt_bytes() + extra > limiet:
		frappe.throw(_("Het opslagquotum van deze omgeving is vol"))


def _bestand(bestand: str, actie: str):
	doc = frappe.db.get_value(
		"Opslagbestand", bestand, ["name", "ruimte", "map", "naam", "huidige_versie", "verwijderd_op"], as_dict=True
	)
	if not doc:
		frappe.throw(_("Bestand bestaat niet"), frappe.DoesNotExistError)
	vereis_op_plek(doc.ruimte, doc.map, actie, doc.name)
	return doc


def _actief_slot(bestand: str):
	return frappe.db.get_value(
		"Opslagvergrendeling",
		{"bestand": bestand, "verloopt": (">", now_datetime())},
		["name", "gebruiker", "apparaat", "reden", "verloopt"],
		as_dict=True,
	)


# --- bladeren en versies -----------------------------------------------------------------------------


@frappe.whitelist()
def bladeren(ruimte: str, map: str | None = None) -> dict:
	map = _controleer_map(ruimte, map)
	vereis_op_plek(ruimte, map, "read")
	mappen = frappe.get_all(
		"Opslagmap",
		filters={"ruimte": ruimte, "bovenliggende": map or ("is", "not set"), "verwijderd_op": ("is", "not set")},
		fields=["name", "naam", "modified"],
		order_by="naam asc",
		ignore_permissions=True,
	)
	bestanden = frappe.get_all(
		"Opslagbestand",
		filters={"ruimte": ruimte, "map": map or ("is", "not set"), "verwijderd_op": ("is", "not set")},
		fields=["name", "naam", "grootte", "huidige_versie", "mime", "modified"],
		order_by="naam asc",
		ignore_permissions=True,
	)
	return {"mappen": mappen, "bestanden": bestanden}


@frappe.whitelist()
def versies(bestand: str) -> list[dict]:
	_bestand(bestand, "read")
	return frappe.get_all(
		"Bestandsversie",
		filters={"bestand": bestand},
		fields=["volgnummer", "grootte", "sha256", "bron", "maker", "creation"],
		order_by="volgnummer desc",
		ignore_permissions=True,
	)


# --- uploaden ----------------------------------------------------------------------------------------


@frappe.whitelist()
def upload_start(
	ruimte: str,
	naam: str,
	grootte: int,
	map: str | None = None,
	basis_versie: int | None = None,
	sha256: str | None = None,
	bron: str = "api",
) -> dict:
	"""Begin een upload: geeft een upload-token en per deel een ondertekende URL (8 MiB per deel)."""
	map = _controleer_map(ruimte, map)
	vereis_op_plek(ruimte, map, "write")
	naam = _controleer_naam(naam)
	grootte = cint(grootte)
	if grootte < 0:
		frappe.throw(_("Ongeldige grootte"))
	_controleer_quotum(grootte)

	sleutel = "v/" + frappe.generate_hash(length=32)
	upload_id = s3.start_multipart(sleutel)
	aantal = max(1, math.ceil(grootte / s3.DEELGROOTTE))
	token = frappe.generate_hash(length=32)
	frappe.cache.set_value(
		UPLOAD_SLEUTEL + token,
		{
			"gebruiker": frappe.session.user,
			"ruimte": ruimte,
			"map": map,
			"naam": naam,
			"grootte": grootte,
			"basis_versie": None if basis_versie in (None, "") else cint(basis_versie),
			"sha256": sha256,
			"bron": bron if bron in BRONNEN else "api",
			"sleutel": sleutel,
			"upload_id": upload_id,
		},
		expires_in_sec=UPLOAD_GELDIG_SECONDEN,
	)
	return {
		"upload": token,
		"deelgrootte": s3.DEELGROOTTE,
		"delen": [{"nummer": n, "url": s3.deel_url(sleutel, upload_id, n)} for n in range(1, aantal + 1)],
	}


@frappe.whitelist()
def upload_klaar(upload: str, delen) -> dict:
	"""Rond de upload af en registreer de versie (of een conflictkopie)."""
	info = frappe.cache.get_value(UPLOAD_SLEUTEL + (upload or ""))
	if not info or info["gebruiker"] != frappe.session.user:
		frappe.throw(_("Upload onbekend of verlopen"), frappe.PermissionError)
	vereis_op_plek(info["ruimte"], info["map"], "write")
	if isinstance(delen, str):
		delen = json.loads(delen)

	s3.voltooi(info["sleutel"], info["upload_id"], delen)
	if s3.grootte(info["sleutel"]) != info["grootte"]:
		frappe.throw(_("Ontvangen grootte wijkt af van de aangekondigde grootte"))

	ruimte, map, naam = info["ruimte"], info["map"], info["naam"]
	bestaand = _actief_bestand(ruimte, map, naam)
	conflict = False
	slot = _actief_slot(bestaand.name) if bestaand else None
	slot_van_ander = bool(slot and slot.gebruiker != frappe.session.user)
	if (
		bestaand
		and not slot_van_ander
		and info["basis_versie"] is not None
		and info["basis_versie"] == cint(bestaand.huidige_versie)
	):
		doc = frappe.get_doc("Opslagbestand", bestaand.name)
		soort = "gewijzigd"
	else:
		if bestaand:
			conflict = True
			naam = _conflictnaam(ruimte, map, naam)
		doc = frappe.get_doc(
			{"doctype": "Opslagbestand", "ruimte": ruimte, "map": map, "naam": naam, "huidige_versie": 0}
		).insert(ignore_permissions=True)
		soort = "nieuw"

	volgnummer = cint(doc.huidige_versie) + 1
	frappe.get_doc(
		{
			"doctype": "Bestandsversie",
			"bestand": doc.name,
			"ruimte": ruimte,
			"volgnummer": volgnummer,
			"s3_sleutel": info["sleutel"],
			"grootte": info["grootte"],
			"sha256": info["sha256"],
			"bron": info["bron"],
			"maker": frappe.session.user,
		}
	).insert(ignore_permissions=True)
	doc.huidige_versie = volgnummer
	doc.grootte = info["grootte"]
	doc.save(ignore_permissions=True)
	_log(ruimte, soort, "Opslagbestand", doc.name, doc.naam)
	frappe.cache.delete_value(UPLOAD_SLEUTEL + upload)
	return {"bestand": doc.name, "naam": doc.naam, "versie": volgnummer, "conflict": conflict}


# --- downloaden --------------------------------------------------------------------------------------


@frappe.whitelist()
def download(bestand: str, versie: int | None = None) -> dict:
	doc = _bestand(bestand, "read")
	volgnummer = cint(versie) or cint(doc.huidige_versie)
	sleutel = frappe.db.get_value("Bestandsversie", {"bestand": bestand, "volgnummer": volgnummer}, "s3_sleutel")
	if not sleutel:
		frappe.throw(_("Versie bestaat niet"), frappe.DoesNotExistError)
	return {"url": s3.download_url(sleutel, doc.naam), "versie": volgnummer, "geldig_seconden": s3.LINK_GELDIG_SECONDEN}


# --- prullenbak --------------------------------------------------------------------------------------


@frappe.whitelist()
def verwijder(bestand: str) -> None:
	doc = _bestand(bestand, "write")
	if doc.verwijderd_op:
		return
	frappe.db.set_value("Opslagbestand", bestand, "verwijderd_op", now_datetime())
	_log(doc.ruimte, "verwijderd", "Opslagbestand", bestand, doc.naam)


@frappe.whitelist()
def herstel(bestand: str) -> dict:
	doc = _bestand(bestand, "write")
	if not doc.verwijderd_op:
		return {"naam": doc.naam}
	naam = doc.naam
	if _actief_bestand(doc.ruimte, doc.map, naam):
		stam, ext = os.path.splitext(naam)
		naam = f"{stam} (hersteld){ext}"
	frappe.db.set_value("Opslagbestand", bestand, {"verwijderd_op": None, "naam": naam})
	_log(doc.ruimte, "hersteld", "Opslagbestand", bestand, naam)
	return {"naam": naam}


# --- wijzigingen en quotum ---------------------------------------------------------------------------


@frappe.whitelist()
def wijzigingen(ruimte: str, sinds: int = 0, limiet: int = 1000) -> dict:
	"""Wijzigingen in de ruimte na cursor `sinds`, oplopend; de cursor is het volgnummer van de wijziging."""
	vereis(ruimte, "read")
	sinds, limiet = cint(sinds), min(max(cint(limiet), 1), 5000)
	regels = frappe.get_all(
		"Opslagwijziging",
		filters={"ruimte": ruimte, "name": (">", sinds)},
		fields=["name", "soort", "doel_doctype", "doel", "naam", "creation"],
		order_by="name asc",
		limit=limiet,
		ignore_permissions=True,
	)
	if regels:
		cursor = cint(regels[-1]["name"])
	else:
		hoogste = frappe.db.sql("select coalesce(max(name), 0) from `tabOpslagwijziging`")[0][0]
		cursor = max(sinds, cint(hoogste))
	return {
		"cursor": cursor,
		"meer": len(regels) == limiet,
		"wijzigingen": [{**r, "volgnummer": cint(r.pop("name"))} for r in regels],
	}


@frappe.whitelist()
def quotum() -> dict:
	limiet = _limiet_bytes()
	return {"gebruikt_bytes": _gebruikt_bytes(), "limiet_bytes": limiet or None}


# --- informatie en vergrendelen (ontwerp §6) ---------------------------------------------------------


@frappe.whitelist()
def info(bestand: str) -> dict:
	doc = _bestand(bestand, "read")
	slot = _actief_slot(bestand)
	return {
		"bestand": doc.name,
		"naam": doc.naam,
		"ruimte": doc.ruimte,
		"map": doc.map,
		"huidige_versie": cint(doc.huidige_versie),
		"verwijderd": bool(doc.verwijderd_op),
		"vergrendeling": slot,
	}


@frappe.whitelist()
def vergrendel(bestand: str, apparaat: str | None = None, reden: str = "handmatig") -> dict:
	"""Uitchecken: alleen jij kunt nu een nieuwe versie maken. Opnieuw aanroepen verlengt het slot."""
	doc = _bestand(bestand, "write")
	verloopt = add_to_date(now_datetime(), hours=SLOT_UREN)
	slot = _actief_slot(bestand)
	if slot and slot.gebruiker != frappe.session.user:
		naam = frappe.db.get_value("User", slot.gebruiker, "full_name") or slot.gebruiker
		frappe.throw(_("Dit bestand wordt bewerkt door {0} tot {1}").format(naam, slot.verloopt))
	frappe.db.delete("Opslagvergrendeling", {"bestand": bestand})
	frappe.get_doc(
		{
			"doctype": "Opslagvergrendeling",
			"bestand": bestand,
			"ruimte": doc.ruimte,
			"gebruiker": frappe.session.user,
			"apparaat": apparaat,
			"reden": reden if reden in ("handmatig", "automatisch") else "handmatig",
			"verloopt": verloopt,
		}
	).insert(ignore_permissions=True)
	if not slot:
		_log(doc.ruimte, "vergrendeld", "Opslagbestand", bestand, doc.naam)
	return {"gebruiker": frappe.session.user, "apparaat": apparaat, "verloopt": verloopt}


@frappe.whitelist()
def ontgrendel(bestand: str) -> None:
	doc = _bestand(bestand, "write")
	slot = _actief_slot(bestand)
	if not slot:
		return
	if slot.gebruiker != frappe.session.user:
		frappe.throw(_("Alleen wie het bestand vergrendelde kan het ontgrendelen"), frappe.PermissionError)
	frappe.db.delete("Opslagvergrendeling", {"bestand": bestand})
	_log(doc.ruimte, "ontgrendeld", "Opslagbestand", bestand, doc.naam)


@frappe.whitelist()
def forceer_ontgrendel(bestand: str) -> None:
	"""Een slot openbreken (alleen beheerders van de ruimte); komt in de wijzigingenlijst."""
	doc = _bestand(bestand, "read")
	if not mag(doc.ruimte, "write", ruimte_zelf=True):
		frappe.throw(_("Alleen een beheerder van de ruimte kan een slot openbreken"), frappe.PermissionError)
	frappe.db.delete("Opslagvergrendeling", {"bestand": bestand})
	_log(doc.ruimte, "ontgrendeld", "Opslagbestand", bestand, doc.naam)


# --- delen (ontwerp §3, B8) --------------------------------------------------------------------------


@frappe.whitelist()
def deel(
	doel_doctype: str,
	doel: str,
	soort: str = "link",
	gebruiker: str | None = None,
	rechten: str = "lezen",
	wachtwoord: str | None = None,
	verloopt: str | None = None,
) -> dict:
	"""Deel een map of bestand: als link zonder account, of met een (gast)gebruiker."""
	from frappe.utils.password import passlibctx

	if doel_doctype not in ("Opslagmap", "Opslagbestand"):
		frappe.throw(_("Alleen mappen en bestanden kunnen gedeeld worden"))
	if doel_doctype == "Opslagbestand":
		ruimte, map = frappe.db.get_value("Opslagbestand", doel, ["ruimte", "map"]) or (None, None)
		vereis_op_plek(ruimte, map, "write", doel)
	else:
		ruimte = frappe.db.get_value("Opslagmap", doel, "ruimte")
		vereis_op_plek(ruimte, doel, "write")
	if soort not in ("link", "gebruiker"):
		frappe.throw(_("Onbekende soort deling"))
	if soort == "gebruiker" and not (gebruiker and frappe.db.exists("User", gebruiker)):
		frappe.throw(_("Kies een bestaande gebruiker om mee te delen"))
	deling = frappe.get_doc(
		{
			"doctype": "Opslagdeling",
			"ruimte": ruimte,
			"doel_doctype": doel_doctype,
			"doel": doel,
			"soort": soort,
			"gebruiker": gebruiker if soort == "gebruiker" else None,
			"rechten": "schrijven" if rechten == "schrijven" and soort == "gebruiker" else "lezen",
			"token": frappe.generate_hash(length=40) if soort == "link" else None,
			"wachtwoord_hash": passlibctx.hash(wachtwoord) if wachtwoord else None,
			"verloopt": verloopt,
		}
	).insert(ignore_permissions=True)
	_log(ruimte, "gedeeld", doel_doctype, doel)
	return {"deling": deling.name, "token": deling.token, "rechten": deling.rechten}


@frappe.whitelist()
def delingen(doel_doctype: str, doel: str) -> list[dict]:
	ruimte = frappe.db.get_value(doel_doctype, doel, "ruimte") if doel_doctype in ("Opslagmap", "Opslagbestand") else None
	vereis(ruimte, "read")
	return frappe.get_all(
		"Opslagdeling",
		filters={"doel_doctype": doel_doctype, "doel": doel, "ingetrokken": 0},
		fields=["name", "soort", "gebruiker", "rechten", "verloopt", "token", "creation"],
		ignore_permissions=True,
	)


@frappe.whitelist()
def trek_in(deling: str) -> None:
	rij = frappe.db.get_value("Opslagdeling", deling, ["ruimte", "doel_doctype", "doel"], as_dict=True)
	if not rij:
		frappe.throw(_("Deling bestaat niet"), frappe.DoesNotExistError)
	vereis(rij.ruimte, "write")
	frappe.db.set_value("Opslagdeling", deling, "ingetrokken", 1)
	_log(rij.ruimte, "ingetrokken", rij.doel_doctype, rij.doel)
