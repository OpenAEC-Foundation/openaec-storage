"""API van OpenAEC Storage (fase 4a): bladeren, uploaden in delen naar S3, downloaden, versies,
prullenbak, wijzigingenlijst en quotum. Rechten volgen uit de ruimte (zie rechten.py).

Gedeeld met mij: wie geen lid is maar een map gedeeld kreeg, gebruikt die map als bovenste map met de parameter
`deling` (bij `bladeren`, `boom`, `wijzigingen`, `maak_map`, `maak_mappen` en `upload_klein`); de rechten komen uit de
deling, en paden die buiten de gedeelde map vallen, worden geweigerd (`buiten_deling`). De methodes per bestand
(downloaden, uploaden, verplaatsen, verwijderen) volgen de rechten op de plek, dus ook die van de deling.

Conflictregel (ontwerp §6): bij `upload_klaar` wint een upload alleen als `basis_versie` gelijk is aan de
huidige versie; anders wordt het een conflictkopie naast het origineel. Er gaat nooit iets verloren.
"""

import hashlib
import json
import math
import os

import frappe
from frappe import _
from frappe.utils import get_url, add_to_date, cint, flt, now_datetime

from openaec_storage import mapsjablonen, namen, s3
from openaec_storage.rechten import (
	RANG,
	actieve_deling,
	eigen_recht,
	heeft_deling_in,
	mag,
	mag_delen,
	mag_ruimte_maken,
	vereis,
	vereis_delen,
	vereis_op_plek,
	voorouders,
	weiger,
	weiger_als_gearchiveerd,
	weiger_deling,
)

UPLOAD_SLEUTEL = "openaec_storage:upload:"
UPLOAD_GELDIG_SECONDEN = 7 * 24 * 3600
BRONNEN = ("web", "sync", "webdav", "api")
SLOT_UREN = 8


# --- hulpfuncties ------------------------------------------------------------------------------------


def _controleer_naam(naam: str, ruimte: str | None = None, map: str | None = None) -> str:
	"""Weiger een ongeldige naam (zie namen.py) met de foutcode `ongeldige_naam`. Met `ruimte` mag een bestaand
	bestand op die plek met precies deze naam blijven heten zoals het heet (een nieuwe versie van een bestand van
	vóór deze regel); een nieuw bestand, een nieuwe map of een nieuwe naam bij hernoemen moet geldig zijn."""
	naam = (naam or "").strip()
	reden = namen.ongeldige_naam(naam)
	if reden and not (ruimte and naam and _actief_bestand(ruimte, map, naam)):
		weiger(_("Ongeldige naam: {0}").format(reden), "ongeldige_naam")
	return naam


def _controleer_map(ruimte: str, map: str | None) -> str | None:
	if not map:
		return None
	if frappe.db.get_value("Opslagmap", map, "ruimte") != ruimte:
		frappe.throw(_("Map hoort niet bij deze ruimte"))
	return map


def _wortel(ruimte: str | None, deling: str | None) -> tuple[str | None, str | None]:
	"""Ruimte en bovenste map van een aanroep: zonder `deling` de ruimte zelf (wortel None), met `deling` de gedeelde
	map (een deling met de ingelogde gebruiker die nu geldt, zie rechten.actieve_deling). `ruimte` mag dan weg."""
	if not deling:
		return ruimte, None
	rij = actieve_deling(deling)
	if ruimte and ruimte != rij.ruimte:
		frappe.throw(_("Deze deling hoort niet bij deze ruimte"))
	if rij.doel_doctype != "Opslagmap":
		weiger(_("Een gedeeld bestand kun je openen en downloaden, maar niet als map gebruiken"), "deling_geen_map")
	return rij.ruimte, rij.doel


def _binnen(wortel: str | None, map: str | None) -> None:
	"""Weiger een map buiten de gedeelde map (de wortel zelf mag); zonder wortel mag alles in de ruimte."""
	if wortel and wortel not in voorouders(map):
		weiger_deling("buiten_deling")


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


def _log(ruimte: str, soort: str, doctype: str, doel: str, naam: str | None = None, namens: str | None = None) -> None:
	"""Regel in de wijzigingenlijst. De aanroeper staat in `owner`; `namens` (zie rechten.namens) is voor wie het
	beheer de handeling uitvoerde."""
	frappe.get_doc(
		{
			"doctype": "Opslagwijziging",
			"ruimte": ruimte,
			"soort": soort,
			"doel_doctype": doctype,
			"doel": doel,
			"naam": naam,
			"namens": namens,
		}
	).insert(ignore_permissions=True)


def _gebruikt_bytes() -> int:
	"""Alle versies in de ruimtes plus de overdrachten die nog bestaan."""
	from openaec_storage import overdracht

	return cint(frappe.db.sql("select coalesce(sum(grootte), 0) from `tabBestandsversie`")[0][0]) + overdracht.gebruikt_bytes()


def _limiet_bytes() -> int:
	return int(flt(frappe.conf.get("openaec_storage_quotum_gb")) * 1_000_000_000)


def _gebruikt_bytes_ruimte(ruimte: str) -> int:
	"""Alle versies in de ruimte (ook oudere versies en de prullenbak), net als bij de omgeving."""
	return cint(
		frappe.db.sql("select coalesce(sum(grootte), 0) from `tabBestandsversie` where ruimte = %s", ruimte)[0][0]
	)


def _limiet_bytes_ruimte(ruimte: str) -> int:
	"""Het eigen quotum van de ruimte (`quotum_gb`); 0 = geen eigen limiet, alleen die van de omgeving."""
	return int(flt(frappe.db.get_value("Ruimte", ruimte, "quotum_gb")) * 1_000_000_000)


def _controleer_quotum(extra: int, ruimte: str | None = None) -> None:
	"""Past er `extra` bij? Eerst de pool van de omgeving, dan (met `ruimte`) het eigen quotum van die ruimte. De
	quota van de ruimtes samen mogen groter zijn dan de pool (overboeking): wat het eerst vol is, weigert."""
	limiet = _limiet_bytes()
	if limiet and _gebruikt_bytes() + extra > limiet:
		weiger(_("Het opslagquotum van deze omgeving is vol"), "quotum_pool_vol")
	limiet = _limiet_bytes_ruimte(ruimte) if ruimte else 0
	if limiet and _gebruikt_bytes_ruimte(ruimte) + extra > limiet:
		weiger(
			_("Het quotum van deze ruimte ({0} GB) is vol").format("{:g}".format(limiet / 1_000_000_000)),
			"quotum_ruimte_vol",
		)


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


def _weiger_als_vergrendeld(bestand: str, melding: str = "Dit bestand is vergrendeld door {0}") -> None:
	"""Een bestand dat een ander heeft vergrendeld, kan niet verplaatst, hernoemd of verwijderd worden."""
	slot = _actief_slot(bestand)
	if slot and slot.gebruiker != frappe.session.user:
		weiger(_(melding).format(slot.gebruiker), "bestand_vergrendeld", frappe.PermissionError)


# --- bladeren en versies -----------------------------------------------------------------------------


@frappe.whitelist()
def bladeren(ruimte: str | None = None, map: str | None = None, deling: str | None = None) -> dict:
	"""Mappen en bestanden op één plek; met `deling` is de gedeelde map het hoofdniveau (`map` leeg)."""
	ruimte, wortel = _wortel(ruimte, deling)
	map = _controleer_map(ruimte, map or wortel)
	_binnen(wortel, map)
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
	blokhashes=None,
) -> dict:
	"""Begin een upload: geeft een upload-token en per te uploaden deel een ondertekende URL.

	Met `blokhashes` (één hash per blok) en een `basis_versie` gelijk aan de huidige versie worden ongewijzigde
	blokken niet opnieuw geüpload maar server-side uit de vorige versie gekopieerd (delta-sync, ontwerp §5).
	"""
	map = _controleer_map(ruimte, map)
	vereis_op_plek(ruimte, map, "write", _bestaand_id(ruimte, map, naam))
	naam = _controleer_naam(naam, ruimte, map)
	mapsjablonen.controleer_upload(ruimte, map, naam)
	grootte = cint(grootte)
	if grootte < 0:
		frappe.throw(_("Ongeldige grootte"))
	_controleer_quotum(grootte, ruimte)

	if isinstance(blokhashes, str):
		blokhashes = json.loads(blokhashes)
	blok = s3.blokgrootte(grootte)
	aantal = max(1, math.ceil(grootte / blok))
	if blokhashes is not None and len(blokhashes) != aantal:
		frappe.throw(_("Aantal blokhashes klopt niet met de grootte"))

	hergebruik = _herbruikbare_blokken(ruimte, map, naam, basis_versie, blok, grootte, blokhashes)
	sleutel = s3.nieuwe_sleutel()
	upload_id = s3.start_multipart(sleutel)
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
			"blokgrootte": blok,
			"blokhashes": blokhashes,
			"hergebruik": hergebruik,
		},
		expires_in_sec=UPLOAD_GELDIG_SECONDEN,
	)
	hergebruikt = {h["nummer"] for h in hergebruik}
	return {
		"upload": token,
		"deelgrootte": blok,
		"hergebruik": sorted(hergebruikt),
		"delen": [
			{"nummer": n, "url": s3.deel_url(sleutel, upload_id, n)} for n in range(1, aantal + 1) if n not in hergebruikt
		],
	}


def _bestaand_id(ruimte: str, map: str | None, naam: str | None) -> str | None:
	"""Het bestand dat op deze plek al zo heet: een nieuwe versie ervan mag ook wie alleen dat bestand gedeeld kreeg
	om te bewerken."""
	bestaand = _actief_bestand(ruimte, map, (naam or "").strip()) if ruimte else None
	return bestaand.name if bestaand else None


def _herbruikbare_blokken(ruimte, map, naam, basis_versie, blok, grootte, blokhashes) -> list[dict]:
	"""Blokken die gelijk zijn aan dezelfde positie in de basisversie: die kopieert de server zelf."""
	if not blokhashes or basis_versie in (None, ""):
		return []
	bestaand = _actief_bestand(ruimte, map, naam)
	if not bestaand or cint(basis_versie) != cint(bestaand.huidige_versie):
		return []
	basis = frappe.db.get_value(
		"Bestandsversie",
		{"bestand": bestaand.name, "volgnummer": cint(basis_versie)},
		["s3_sleutel", "grootte", "blokgrootte", "blokhashes"],
		as_dict=True,
	)
	if not basis or cint(basis.blokgrootte) != blok or not basis.blokhashes:
		return []
	oud = json.loads(basis.blokhashes)
	resultaat = []
	for index, hash_nieuw in enumerate(blokhashes):
		if index < len(oud) and oud[index] == hash_nieuw:
			begin = index * blok
			eind = min(begin + blok, cint(basis.grootte), grootte) - 1
			if eind >= begin:
				resultaat.append({"nummer": index + 1, "bron": basis.s3_sleutel, "begin": begin, "eind": eind})
	return resultaat


@frappe.whitelist()
def upload_klaar(upload: str, delen) -> dict:
	"""Rond de upload af en registreer de versie (of een conflictkopie)."""
	info = frappe.cache.get_value(UPLOAD_SLEUTEL + (upload or ""))
	if not info or info["gebruiker"] != frappe.session.user:
		frappe.throw(_("Upload onbekend of verlopen"), frappe.PermissionError)
	# Opnieuw: een deling kan intussen zijn ingetrokken of verlopen (dan een nette fout, ook midden in een sync).
	vereis_op_plek(info["ruimte"], info["map"], "write", _bestaand_id(info["ruimte"], info["map"], info["naam"]))
	mapsjablonen.controleer_upload(info["ruimte"], info["map"], info["naam"])
	if isinstance(delen, str):
		delen = json.loads(delen)

	delen = list(delen) + [
		{
			"nummer": h["nummer"],
			"etag": s3.kopieer_deel(info["sleutel"], info["upload_id"], h["nummer"], h["bron"], h["begin"], h["eind"]),
		}
		for h in info.get("hergebruik") or []
	]
	s3.voltooi(info["sleutel"], info["upload_id"], delen)
	if s3.grootte(info["sleutel"]) != info["grootte"]:
		frappe.throw(_("Ontvangen grootte wijkt af van de aangekondigde grootte"))
	resultaat = _registreer(info)
	frappe.cache.delete_value(UPLOAD_SLEUTEL + upload)
	return resultaat


def _registreer(info: dict) -> dict:
	"""Leg een geüploade inhoud (al in S3 onder `info["sleutel"]`) vast als versie, of als conflictkopie: dezelfde
	regel voor de gewone upload en voor kleine bestanden in één verzoek (`upload_klein`)."""
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
		else:
			# Een nieuw bestand moet een geldige naam hebben (een bestaand bestand met een oude, ongeldige naam mag
			# een nieuwe versie krijgen, maar kan tussen het begin en het eind van de upload verdwenen zijn).
			_controleer_naam(naam)
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
			"sha256": info.get("sha256"),
			"bron": info["bron"],
			"maker": frappe.session.user,
			"blokgrootte": info.get("blokgrootte") or s3.DEELGROOTTE,
			"blokhashes": json.dumps(info["blokhashes"]) if info.get("blokhashes") else None,
		}
	).insert(ignore_permissions=True)
	doc.huidige_versie = volgnummer
	doc.grootte = info["grootte"]
	doc.save(ignore_permissions=True)
	_log(ruimte, soort, "Opslagbestand", doc.name, doc.naam)
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
	# Verwijderen vraagt schrijfrecht op de map: wie alleen dit bestand gedeeld kreeg, kan het bewerken, niet weghalen.
	vereis_op_plek(doc.ruimte, doc.map, "write")
	mapsjablonen.controleer_schrijven(doc.ruimte, doc.map)
	_weiger_als_vergrendeld(bestand)
	frappe.db.set_value("Opslagbestand", bestand, "verwijderd_op", now_datetime())
	_log(doc.ruimte, "verwijderd", "Opslagbestand", bestand, doc.naam)


@frappe.whitelist()
def herstel(bestand: str) -> dict:
	doc = _bestand(bestand, "write")
	if not doc.verwijderd_op:
		return {"naam": doc.naam}
	mapsjablonen.controleer_schrijven(doc.ruimte, doc.map)
	naam = doc.naam
	if _actief_bestand(doc.ruimte, doc.map, naam):
		stam, ext = os.path.splitext(naam)
		naam = f"{stam} (hersteld){ext}"
	frappe.db.set_value("Opslagbestand", bestand, {"verwijderd_op": None, "naam": naam})
	_log(doc.ruimte, "hersteld", "Opslagbestand", bestand, naam)
	return {"naam": naam}


# --- verplaatsen en hernoemen (WebDAV MOVE, verkenner) -----------------------------------------------


def _actieve_map(ruimte: str, bovenliggende: str | None, naam: str):
	return frappe.db.sql(
		"""select name from `tabOpslagmap` where ruimte = %s and ifnull(bovenliggende, '') = %s
		and lower(naam) = lower(%s) and verwijderd_op is null""",
		(ruimte, bovenliggende or "", naam),
	)


@frappe.whitelist()
def verplaats(bestand: str, naam: str, map: str | None = None, overschrijf: int = 0) -> dict:
	"""Hernoem of verplaats een bestand binnen zijn ruimte (`map` leeg = hoofdniveau).

	Bestaat er op de nieuwe plek al een bestand en is `overschrijf` gezet, dan wordt de inhoud een nieuwe versie
	van dat bestand: zo blijven geschiedenis, deellinks en de sync-toestand van het doel behouden (opslaan via een
	tijdelijk bestand en hernoemen). Het verplaatste bestand verdwijnt dan.
	"""
	bron = _bestand(bestand, "write")
	if bron.verwijderd_op:
		frappe.throw(_("Bestand staat in de prullenbak"))
	naam = _controleer_naam(naam)
	map = _controleer_map(bron.ruimte, map or None)
	vereis_op_plek(bron.ruimte, map, "write")
	mapsjablonen.controleer_schrijven(bron.ruimte, bron.map)
	mapsjablonen.controleer_upload(bron.ruimte, map, naam)
	_weiger_als_vergrendeld(bron.name)

	doel = _actief_bestand(bron.ruimte, map, naam)
	if doel and doel.name == bron.name:
		return {"bestand": bron.name, "naam": naam, "versie": cint(bron.huidige_versie)}
	if doel and not cint(overschrijf):
		frappe.throw(_("Er staat al een bestand met deze naam"))
	if _actieve_map(bron.ruimte, map, naam):
		frappe.throw(_("Er staat al een map met deze naam"))

	if not doel:
		frappe.db.set_value("Opslagbestand", bron.name, {"naam": naam, "map": map})
		_log(bron.ruimte, "verplaatst", "Opslagbestand", bron.name, naam)
		return {"bestand": bron.name, "naam": naam, "versie": cint(bron.huidige_versie)}

	_weiger_als_vergrendeld(doel.name, "Het doel is vergrendeld door {0}")
	# De huidige versie van de bron wordt de volgende versie van het doel. Heeft de bron oudere versies, dan gaat
	# hij daarmee naar de prullenbak (niets gaat verloren); anders verdwijnt hij.
	volgnummer = cint(doel.huidige_versie) + 1
	versie = frappe.db.get_value(
		"Bestandsversie", {"bestand": bron.name, "volgnummer": bron.huidige_versie}, ["name", "grootte"], as_dict=True
	)
	frappe.db.set_value("Bestandsversie", versie.name, {"bestand": doel.name, "volgnummer": volgnummer})
	frappe.db.set_value("Opslagbestand", doel.name, {"huidige_versie": volgnummer, "grootte": versie.grootte})
	frappe.db.delete("Opslagvergrendeling", {"bestand": bron.name})
	vorige = frappe.db.get_value(
		"Bestandsversie", {"bestand": bron.name}, ["volgnummer", "grootte"], order_by="volgnummer desc", as_dict=True
	)
	if vorige:
		frappe.db.set_value(
			"Opslagbestand",
			bron.name,
			{"huidige_versie": vorige.volgnummer, "grootte": vorige.grootte, "verwijderd_op": now_datetime()},
		)
	else:
		frappe.db.delete("Opslagbestand", {"name": bron.name})
	_log(bron.ruimte, "verwijderd", "Opslagbestand", bron.name, bron.naam)
	_log(bron.ruimte, "gewijzigd", "Opslagbestand", doel.name, naam)
	return {"bestand": doel.name, "naam": naam, "versie": volgnummer}


def _submappen(map: str, ook_verwijderd: bool = False) -> list[str]:
	"""De map zelf en alle mappen eronder (zonder de prullenbak, tenzij `ook_verwijderd`)."""
	filters = {} if ook_verwijderd else {"verwijderd_op": ("is", "not set")}
	uit, te_doen = [], [map]
	while te_doen:
		huidig = te_doen.pop()
		uit.append(huidig)
		te_doen += frappe.get_all("Opslagmap", filters={"bovenliggende": huidig, **filters}, pluck="name")
	return uit


@frappe.whitelist()
def verplaats_map(map: str, naam: str, bovenliggende: str | None = None) -> dict:
	"""Hernoem of verplaats een map binnen zijn ruimte (`bovenliggende` leeg = hoofdniveau)."""
	doc = frappe.db.get_value("Opslagmap", map, ["name", "ruimte", "bovenliggende", "verwijderd_op"], as_dict=True)
	if not doc or doc.verwijderd_op:
		frappe.throw(_("Map bestaat niet"), frappe.DoesNotExistError)
	vereis_op_plek(doc.ruimte, doc.name, "write")
	naam = _controleer_naam(naam)
	bovenliggende = _controleer_map(doc.ruimte, bovenliggende or None)
	vereis_op_plek(doc.ruimte, bovenliggende, "write")
	mapsjablonen.controleer_schrijven(doc.ruimte, doc.bovenliggende)
	mapsjablonen.controleer_schrijven(doc.ruimte, doc.name)
	mapsjablonen.controleer_schrijven(doc.ruimte, bovenliggende)
	if bovenliggende and bovenliggende in _submappen(doc.name):
		frappe.throw(_("Een map kan niet in zichzelf worden geplaatst"))
	bestaand = _actieve_map(doc.ruimte, bovenliggende, naam)
	if bestaand and bestaand[0][0] != doc.name:
		frappe.throw(_("Er staat al een map met deze naam"))
	if _actief_bestand(doc.ruimte, bovenliggende, naam):
		frappe.throw(_("Er staat al een bestand met deze naam"))
	frappe.db.set_value("Opslagmap", doc.name, {"naam": naam, "bovenliggende": bovenliggende})
	_log(doc.ruimte, "verplaatst", "Opslagmap", doc.name, naam)
	return {"map": doc.name, "naam": naam}


@frappe.whitelist()
def verwijder_map(map: str) -> None:
	"""Map met alles erin naar de prullenbak (zelfde tijdstip, zodat herstellen per map kan)."""
	doc = frappe.db.get_value(
		"Opslagmap", map, ["name", "ruimte", "naam", "bovenliggende", "verwijderd_op"], as_dict=True
	)
	if not doc or doc.verwijderd_op:
		return
	vereis_op_plek(doc.ruimte, doc.name, "write")
	# Ook op de map erboven: de gedeelde map zelf haalt wie hem gedeeld kreeg niet weg (`buiten_deling`).
	vereis_op_plek(doc.ruimte, doc.bovenliggende, "write")
	moment = now_datetime()
	mappen = _submappen(doc.name)
	# Ook een alleen-lezenmap diep in de map gaat niet zomaar mee naar de prullenbak.
	mapsjablonen.controleer_schrijven(doc.ruimte, doc.bovenliggende)
	mapsjablonen.controleer_schrijven(doc.ruimte, doc.name)
	mapsjablonen.controleer_boom(doc.ruimte, mappen)
	bestanden = frappe.get_all(
		"Opslagbestand", filters={"map": ("in", mappen), "verwijderd_op": ("is", "not set")}, pluck="name"
	)
	# Een bestand dat een ander heeft vergrendeld, gaat ook niet via zijn map naar de prullenbak.
	for b in bestanden:
		_weiger_als_vergrendeld(b, "In deze map staat een bestand dat vergrendeld is door {0}")
	for m in mappen:
		frappe.db.set_value("Opslagmap", m, "verwijderd_op", moment)
	for b in bestanden:
		frappe.db.set_value("Opslagbestand", b, "verwijderd_op", moment)
	_log(doc.ruimte, "verwijderd", "Opslagmap", doc.name, doc.naam)


# --- wijzigingen en quotum ---------------------------------------------------------------------------


@frappe.whitelist()
def wijzigingen(ruimte: str | None = None, sinds: int = 0, limiet: int = 1000, deling: str | None = None) -> dict:
	"""Wijzigingen in de ruimte na cursor `sinds`, oplopend; de cursor is het volgnummer van de wijziging. Met
	`deling` alleen die binnen de gedeelde map (de cursor loopt over de hele ruimte, zoals zonder deling)."""
	ruimte, wortel = _wortel(ruimte, deling)
	if wortel:
		vereis_op_plek(ruimte, wortel, "read")
	else:
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
	meer = len(regels) == limiet
	if wortel:
		regels = _binnen_wortel(wortel, regels)
	return {
		"cursor": cursor,
		"meer": meer,
		"wijzigingen": [{**r, "volgnummer": cint(r.pop("name"))} for r in regels],
	}


def _binnen_wortel(wortel: str, regels: list) -> list:
	"""De regels uit de wijzigingenlijst over de wortel, een map eronder of een bestand daarin (ook verwijderde)."""
	mappen = set(_submappen(wortel, ook_verwijderd=True))
	bestanden = [r.doel for r in regels if r.doel_doctype == "Opslagbestand"]
	in_map = (
		dict(frappe.get_all("Opslagbestand", filters={"name": ("in", bestanden)}, fields=["name", "map"], as_list=True))
		if bestanden
		else {}
	)
	return [
		r
		for r in regels
		if (r.doel_doctype == "Opslagmap" and r.doel in mappen)
		or (r.doel_doctype == "Opslagbestand" and in_map.get(r.doel) in mappen)
	]


@frappe.whitelist()
def quotum_ruimte(ruimte: str) -> dict:
	"""Gebruik en eigen quotum van één ruimte (`limiet_bytes` leeg = geen eigen limiet; de pool geldt altijd). Voor
	de leden van de ruimte en voor een beheerder van de omgeving."""
	if not mag_ruimte_maken() or not frappe.db.exists("Ruimte", ruimte or ""):
		vereis(ruimte, "read")
	limiet = _limiet_bytes_ruimte(ruimte)
	return {"gebruikt_bytes": _gebruikt_bytes_ruimte(ruimte), "limiet_bytes": limiet or None}


@frappe.whitelist()
def quotum() -> dict:
	from openaec_storage import overdracht

	limiet = _limiet_bytes()
	return {
		"gebruikt_bytes": _gebruikt_bytes(),
		"limiet_bytes": limiet or None,
		"overdrachten_bytes": overdracht.gebruikt_bytes(),
	}


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
	mapsjablonen.controleer_schrijven(doc.ruimte, doc.map)
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
	weiger_als_gearchiveerd(doc.ruimte)
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
	"""Deel een map of bestand: als link zonder account, of met een (gast)gebruiker. Een link naar een map met
	`rechten="uploaden"` is een uploadlink (bestanden opvragen): de ontvanger kan alleen bestanden aanleveren. Met een
	gebruiker `lezen`, `schrijven` of `beheren`; nooit meer dan je zelf hebt (zie rechten.vereis_delen)."""
	from frappe.utils.password import passlibctx

	if doel_doctype not in ("Opslagmap", "Opslagbestand"):
		frappe.throw(_("Alleen mappen en bestanden kunnen gedeeld worden"))
	ruimte, map, bestand = _plek_van(doel_doctype, doel)
	if soort not in ("link", "gebruiker"):
		frappe.throw(_("Onbekende soort deling"))
	vereis_delen(ruimte, map, bestand, _deelrechten(soort, rechten) if soort == "gebruiker" else None)
	uitnodiging = None
	if soort == "gebruiker" and gebruiker and not frappe.db.exists("User", gebruiker):
		gebruiker, uitnodiging = _nodig_gast_uit(gebruiker, doel_doctype, doel)
	if soort == "gebruiker" and not (gebruiker and frappe.db.exists("User", gebruiker)):
		weiger(_("Kies een bestaande gebruiker om mee te delen"), "gebruiker_onbekend")
	if rechten == "uploaden" and not (soort == "link" and doel_doctype == "Opslagmap"):
		frappe.throw(_("Bestanden opvragen kan alleen met een link naar een map"))
	deling = frappe.get_doc(
		{
			"doctype": "Opslagdeling",
			"ruimte": ruimte,
			"doel_doctype": doel_doctype,
			"doel": doel,
			"soort": soort,
			"gebruiker": gebruiker if soort == "gebruiker" else None,
			"rechten": _deelrechten(soort, rechten),
			"token": frappe.generate_hash(length=40) if soort == "link" else None,
			"wachtwoord_hash": passlibctx.hash(wachtwoord) if wachtwoord else None,
			"verloopt": verloopt,
		}
	).insert(ignore_permissions=True)
	_log(ruimte, "gedeeld", doel_doctype, doel)
	if soort == "gebruiker":
		_meld_ontvanger(deling, uitgenodigd=uitnodiging == "uitgenodigd")
	return {
		"deling": deling.name,
		"token": deling.token,
		"rechten": deling.rechten,
		# Bij een nieuw adres: hoe de uitnodiging als gast ging (al_lid, lid of uitgenodigd); anders null.
		"uitnodiging": uitnodiging,
		"gebruiker": gebruiker if soort == "gebruiker" else None,
		# Volledig publiek adres (host_name van de site): klaar om in een mail of chat te plakken.
		"url": get_url(f"/delen/{deling.token}"),
	}


GAST_UITNODIGEN = "openaec_beheer.gasten.nodig_uit_voor_deling"


def _nodig_gast_uit(email: str, doel_doctype: str, doel: str) -> tuple[str | None, str | None]:
	"""Een adres zonder gebruiker op deze site: als gast uitnodigen met een OpenAEC-account (het account geldt voor
	alle omgevingen; Beheer maakt de gebruiker hier meteen aan). Geeft (gebruiker, status) met status `al_lid`, `lid`
	of `uitgenodigd`; (None, None) als het geen geldig adres is of de site geen Beheer heeft."""
	email = (email or "").strip().lower()
	if not frappe.utils.validate_email_address(email):
		return None, None
	try:
		uitnodigen = frappe.get_attr(GAST_UITNODIGEN)
	except (AttributeError, ImportError):
		return None, None
	# Fouten van Beheer (geen recht, te veel uitnodigingen) gaan met hun eigen foutcode door naar de aanroeper.
	uit = uitnodigen(email=email, doel_doctype=doel_doctype, doel=doel) or {}
	return uit.get("gebruiker") or email, uit.get("status")


def _plek_van(doel_doctype: str, doel: str) -> tuple[str | None, str | None, str | None]:
	"""Ruimte, map en (bij een bestand) bestand van het doel van een deling."""
	if doel_doctype == "Opslagbestand":
		ruimte, map = frappe.db.get_value("Opslagbestand", doel, ["ruimte", "map"]) or (None, None)
		return ruimte, map, doel
	if doel_doctype == "Opslagmap":
		return frappe.db.get_value("Opslagmap", doel, "ruimte"), doel, None
	return None, None, None


def _deelrechten(soort: str, rechten: str) -> str:
	"""Schrijven en beheren alleen voor een gebruiker; uploaden (bestanden opvragen) alleen voor een link."""
	if soort == "gebruiker" and rechten in ("schrijven", "beheren"):
		return rechten
	if soort == "link" and rechten == "uploaden":
		return "uploaden"
	return "lezen"


# Per recht: de rol zoals de ontvanger hem leest, en wat hij ermee kan (in de mail).
ROLNAMEN = {
	"lezen": ("kan bekijken", "bekijken en downloaden"),
	"schrijven": ("kan bewerken", "bekijken, bewerken en nieuwe bestanden toevoegen"),
	"beheren": ("kan beheren", "bekijken en bewerken, en verder delen met anderen"),
}


def _meld_ontvanger(deling, uitgenodigd: bool = False) -> None:
	"""Melding (bel in de desk) en e-mail aan wie er net een map of bestand mee gedeeld kreeg, met de naam, wie het
	deelde en een link naar /opslag. Via de mail van de site (in de wachtrij); is er geen uitgaande mail, dan staat
	dat in het foutlog en blijft de deling gewoon staan. Bij intrekken volgt geen mail. `uitgenodigd`: de ontvanger
	kreeg net een uitnodiging voor een OpenAEC-account; de mail zegt dan dat hij die eerst aanvaardt."""
	from frappe.utils import escape_html, get_fullname

	if not deling.gebruiker or deling.gebruiker == frappe.session.user:
		return
	naam = frappe.db.get_value(deling.doel_doctype, deling.doel, "naam") or deling.doel
	door = get_fullname(frappe.session.user) or frappe.session.user
	ruimte_naam = frappe.db.get_value("Ruimte", deling.ruimte, "naam") or deling.ruimte
	wat = _("de map {0}") if deling.doel_doctype == "Opslagmap" else _("het bestand {0}")
	rol = ROLNAMEN.get(deling.rechten, ROLNAMEN["lezen"])
	onderwerp = _("{0} heeft {1} met je gedeeld ({2})").format(door, wat.format(naam), _(rol[0]))
	rechten = _(rol[1])
	pad = f"/opslag?deling={deling.name}"
	try:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": deling.gebruiker,
				"from_user": frappe.session.user,
				"type": "Alert",
				"subject": escape_html(onderwerp),
				"document_type": deling.doel_doctype,
				"document_name": deling.doel,
				"link": pad,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Melding over een deling niet gemaakt")
	url = get_url(pad)
	verloopt = (
		"<p style='color:#6b6b76'>"
		+ escape_html(_("Gedeeld tot {0}.").format(frappe.utils.format_datetime(deling.verloopt, "d MMMM yyyy HH:mm")))
		+ "</p>"
		if deling.verloopt
		else ""
	)
	html = (
		f"<p>{escape_html(onderwerp)} ({escape_html(ruimte_naam)}).</p>"
		f"<p>{escape_html(_('Je kunt het {0}.').format(rechten))}</p>"
		f"<p><a href='{escape_html(url)}' style='display:inline-block;background:#d97706;color:#fff;padding:10px 18px;"
		f"border-radius:6px;text-decoration:none'>{escape_html(_('Openen'))}</a></p>"
		f"<p>{escape_html(url)}</p>{verloopt}"
	)
	if uitgenodigd:
		html += (
			"<p style='color:#6b6b76'>"
			+ escape_html(
				_("Je hebt nog geen OpenAEC-account: maak het eerst aan via de uitnodiging die je apart per e-mail kreeg.")
			)
			+ "</p>"
		)
	try:
		frappe.sendmail(
			recipients=[deling.gebruiker],
			subject=onderwerp,
			message=html,
			reference_doctype="Opslagdeling",
			reference_name=deling.name,
		)
	except Exception:
		frappe.log_error(title="Mail over een deling niet verstuurd")


@frappe.whitelist()
def gedeeld_met_mij() -> list[dict]:
	"""Wat anderen met de ingelogde gebruiker deelden (soort `gebruiker`), nieuwste eerst: alleen delingen die nu
	gelden (niet ingetrokken, niet verlopen, het doel niet verwijderd) in ruimtes die niet gearchiveerd zijn. Per map of
	bestand één regel, met het hoogste recht. `al_lid` (met `rol`): je bent ook lid van de ruimte; de deling kan dan
	meer rechten geven dan je rol. Een gedeeld bestand heeft ook `map`, `grootte` en `versie` (om te openen en, bij
	`schrijven`, een nieuwe versie te uploaden)."""
	gebruiker = frappe.session.user
	rijen = frappe.db.sql(
		"""select d.name, d.ruimte, r.naam as ruimte_naam, d.doel_doctype, d.doel, d.rechten, d.verloopt, d.owner,
		d.creation from `tabOpslagdeling` d join `tabRuimte` r on r.name = d.ruimte
		where d.soort = 'gebruiker' and d.gebruiker = %(gebruiker)s and ifnull(d.ingetrokken, 0) = 0
		and (d.verloopt is null or d.verloopt >= %(nu)s) and ifnull(r.gearchiveerd, 0) = 0
		order by d.creation desc, d.name""",
		{"gebruiker": gebruiker, "nu": now_datetime()},
		as_dict=True,
	)
	rollen = dict(
		frappe.db.sql("select parent, rol from `tabRuimtelid` where parenttype = 'Ruimte' and gebruiker = %s", gebruiker)
	)
	namen: dict[str, str] = {}
	per_doel: dict[tuple[str, str], dict] = {}
	for r in rijen:
		sleutel = (r.doel_doctype, r.doel)
		if sleutel in per_doel:
			# Dezelfde map of hetzelfde bestand nog eens gedeeld: het hoogste recht telt.
			if RANG.get(r.rechten, 0) > RANG.get(per_doel[sleutel]["rechten"], 0):
				per_doel[sleutel].update({"deling": r.name, "rechten": r.rechten, "verloopt": r.verloopt})
			continue
		velden = ["naam", "verwijderd_op"]
		if r.doel_doctype == "Opslagbestand":
			velden += ["map", "grootte", "huidige_versie"]
		doel = frappe.db.get_value(r.doel_doctype, r.doel, velden, as_dict=True)
		if not doel or doel.verwijderd_op:
			continue
		if r.owner not in namen:
			namen[r.owner] = frappe.db.get_value("User", r.owner, "full_name") or r.owner
		regel = {
			"deling": r.name,
			"doel_doctype": r.doel_doctype,
			"doel": r.doel,
			"naam": doel.naam,
			"ruimte": r.ruimte,
			"ruimte_naam": r.ruimte_naam,
			"rechten": r.rechten,
			"gedeeld_door": r.owner,
			"gedeeld_door_naam": namen[r.owner],
			"sinds": r.creation,
			"verloopt": r.verloopt,
			"al_lid": r.ruimte in rollen,
			"rol": rollen.get(r.ruimte),
		}
		if r.doel_doctype == "Opslagbestand":
			regel.update({"map": doel.map, "grootte": cint(doel.grootte), "versie": cint(doel.huidige_versie)})
		per_doel[sleutel] = regel
	return list(per_doel.values())


@frappe.whitelist()
def mijn_delingen() -> list[dict]:
	"""Alle actieve delingen die de gebruiker zelf maakte, nieuwste eerst: om te beheren en in te trekken."""
	rijen = frappe.get_all(
		"Opslagdeling",
		filters={"owner": frappe.session.user, "ingetrokken": 0},
		fields=["name", "ruimte", "doel_doctype", "doel", "soort", "gebruiker", "rechten", "verloopt", "token",
			"wachtwoord_hash", "creation"],
		order_by="creation desc",
		ignore_permissions=True,
	)
	nu = now_datetime()
	resultaat = []
	for r in rijen:
		doel = frappe.db.get_value(r.doel_doctype, r.doel, ["naam", "verwijderd_op"], as_dict=True)
		if not doel or doel.verwijderd_op:
			continue
		resultaat.append(
			{
				"deling": r.name,
				"ruimte": r.ruimte,
				"ruimte_naam": frappe.db.get_value("Ruimte", r.ruimte, "naam"),
				"doel_doctype": r.doel_doctype,
				"doel": r.doel,
				"naam": doel.naam,
				"soort": r.soort,
				"gebruiker": r.gebruiker,
				"rechten": r.rechten,
				"verloopt": r.verloopt,
				"verlopen": bool(r.verloopt and r.verloopt < nu),
				"beveiligd": bool(r.wachtwoord_hash),
				"url": get_url(f"/delen/{r.token}") if r.soort == "link" and r.token else None,
				"gemaakt": r.creation,
			}
		)
	return resultaat


@frappe.whitelist()
def delingen(doel_doctype: str, doel: str) -> list[dict]:
	"""De niet ingetrokken delingen van een map of bestand (ook verlopen, met `verlopen`), met of er een wachtwoord op
	zit (`beveiligd`; nooit de hash) en wie de deling maakte. Voor wie de ruimte kan lezen en wie de plek via een
	deling mag beheren."""
	ruimte, map, bestand = _plek_van(doel_doctype, doel)
	if not (ruimte and mag(ruimte, "read")):
		vereis_op_plek(ruimte, map, "read", bestand)
		if eigen_recht(ruimte, map, bestand) != "beheren":
			weiger(_("Wie toegang heeft, ziet wie het mag beheren"), "geen_beheerrecht", frappe.PermissionError)
	rijen = frappe.get_all(
		"Opslagdeling",
		filters={"doel_doctype": doel_doctype, "doel": doel, "ingetrokken": 0},
		fields=["name", "soort", "gebruiker", "rechten", "verloopt", "token", "creation", "owner", "wachtwoord_hash"],
		ignore_permissions=True,
	)
	nu = now_datetime()
	namen: dict[str, str] = {}
	for r in rijen:
		r["beveiligd"] = bool(r.pop("wachtwoord_hash"))
		r["verlopen"] = bool(r.verloopt and r.verloopt < nu)
		r["gemaakt_door"] = r.pop("owner")
		if r["gemaakt_door"] not in namen:
			namen[r["gemaakt_door"]] = frappe.db.get_value("User", r["gemaakt_door"], "full_name") or r["gemaakt_door"]
		r["gemaakt_door_naam"] = namen[r["gemaakt_door"]]
	return rijen


@frappe.whitelist()
def collegas(tekst: str = "", ruimte: str | None = None) -> list[dict]:
	"""Collega's om mee te delen: actieve gebruikers van de omgeving (geen websitegebruikers, gasten of
	Administrator), gezocht op e-mailadres en volledige naam, hoogstens 20. `lid_als` is de rol in `ruimte`, of None
	voor wie geen lid is. Alleen voor wie in die ruimte mag delen (schrijver of beheerder) en voor een beheerder van
	de omgeving."""
	if not mag_ruimte_maken():
		if not ruimte:
			frappe.throw(_("Geen toegang"), frappe.PermissionError)
		# Wie in de ruimte iets mag beheren (via een deling), zoekt ook collega's om mee te delen.
		if not heeft_deling_in(ruimte, frappe.session.user, "beheren"):
			vereis(ruimte, "write")
	tekst = (tekst or "").strip()
	# Jokertekens in de zoektekst letterlijk nemen.
	zoek = "%" + tekst.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
	gebruikers = frappe.db.sql(
		"""select name, full_name from `tabUser`
		where enabled = 1 and user_type = 'System User' and name not in ('Administrator', 'Guest')
		and (name like %(zoek)s or full_name like %(zoek)s)
		order by full_name, name limit 20""",
		{"zoek": zoek},
		as_dict=True,
	)
	rollen = {}
	if ruimte:
		rollen = dict(
			frappe.db.sql(
				"select gebruiker, rol from `tabRuimtelid` where parent = %s and parenttype = 'Ruimte'", ruimte
			)
		)
	return [{"gebruiker": g.name, "naam": g.full_name or g.name, "lid_als": rollen.get(g.name)} for g in gebruikers]


@frappe.whitelist()
def trek_in(deling: str) -> None:
	"""Een deling intrekken: een schrijver of beheerder van de ruimte, of wie de plek via een deling mag beheren."""
	rij = frappe.db.get_value("Opslagdeling", deling, ["ruimte", "doel_doctype", "doel"], as_dict=True)
	if not rij:
		frappe.throw(_("Deling bestaat niet"), frappe.DoesNotExistError)
	if not mag(rij.ruimte, "write"):
		ruimte, map, bestand = _plek_van(rij.doel_doctype, rij.doel)
		vereis_delen(ruimte or rij.ruimte, map, bestand, None)
	frappe.db.set_value("Opslagdeling", deling, "ingetrokken", 1)
	_log(rij.ruimte, "ingetrokken", rij.doel_doctype, rij.doel)


@frappe.whitelist(methods=["POST"])
def wijzig_deling(deling: str, rechten: str) -> dict:
	"""De rechten van een deling met een gebruiker wijzigen (`lezen`, `schrijven` of `beheren`): mag wie er ook mee
	mag delen, en nooit naar meer, of van meer, dan je zelf hebt (`meer_dan_eigen_recht`). Komt in de wijzigingenlijst
	(`deling_gewijzigd`); de ontvanger krijgt geen mail."""
	rij = frappe.db.get_value(
		"Opslagdeling", deling, ["name", "ruimte", "doel_doctype", "doel", "soort", "rechten", "ingetrokken"], as_dict=True
	)
	if not rij or cint(rij.ingetrokken):
		frappe.throw(_("Deling bestaat niet"), frappe.DoesNotExistError)
	if rij.soort != "gebruiker":
		frappe.throw(_("Alleen de rechten van een deling met een collega zijn te wijzigen"))
	if rechten not in RANG:
		frappe.throw(_("Onbekende rechten: kies lezen, schrijven of beheren"))
	ruimte, map, bestand = _plek_van(rij.doel_doctype, rij.doel)
	vereis_delen(ruimte, map, bestand, rechten, rij.rechten)
	if rechten != rij.rechten:
		frappe.db.set_value("Opslagdeling", deling, "rechten", rechten)
		_log(rij.ruimte, "deling_gewijzigd", rij.doel_doctype, rij.doel, rechten)
	return {"deling": deling, "rechten": rechten}


@frappe.whitelist()
def toegang(doel_doctype: str, doel: str) -> dict:
	"""Wat de ingelogde gebruiker met een map of bestand mag, voor het deelvenster: `rechten` (zijn hoogste recht:
	`lezen`, `schrijven` of `beheren`, uit zijn rol of een deling), `mag_delen` (delen, en de delingen hier wijzigen en
	intrekken) en `max_rechten` (het hoogste recht dat hij een ander mag geven, of None)."""
	ruimte, map, bestand = _plek_van(doel_doctype, doel)
	vereis_op_plek(ruimte, map, "read", bestand)
	eigen = eigen_recht(ruimte, map, bestand)
	kan = mag_delen(ruimte, map, bestand) and not frappe.db.get_value("Ruimte", ruimte, "gearchiveerd")
	return {"rechten": eigen, "mag_delen": bool(kan), "max_rechten": eigen if kan else None}


@frappe.whitelist()
def blokken(bestand: str, versie: int | None = None) -> dict:
	"""Blokgrootte en blokhashes van een versie: de client downloadt alleen blokken die lokaal anders zijn."""
	doc = _bestand(bestand, "read")
	volgnummer = cint(versie) or cint(doc.huidige_versie)
	rij = frappe.db.get_value(
		"Bestandsversie", {"bestand": bestand, "volgnummer": volgnummer}, ["blokgrootte", "blokhashes", "grootte"], as_dict=True
	)
	if not rij:
		frappe.throw(_("Versie bestaat niet"), frappe.DoesNotExistError)
	return {
		"versie": volgnummer,
		"grootte": cint(rij.grootte),
		"blokgrootte": cint(rij.blokgrootte) or s3.DEELGROOTTE,
		"hashes": json.loads(rij.blokhashes) if rij.blokhashes else None,
	}


# --- ruimtes en mappen -------------------------------------------------------------------------------


@frappe.whitelist()
def ruimtes() -> list[dict]:
	"""De ruimtes waar de ingelogde gebruiker lid van is, met zijn rol. Gearchiveerde ruimtes staan er niet in (de
	desktop-app synct ze dan niet meer); het beheer ziet ze via `ruimtebeheer.lijst`."""
	return frappe.db.sql(
		"""select r.name, r.naam, r.soort, l.rol from `tabRuimte` r
		join `tabRuimtelid` l on l.parent = r.name and l.parenttype = 'Ruimte'
		where l.gebruiker = %s and ifnull(r.gearchiveerd, 0) = 0 order by r.naam""",
		frappe.session.user,
		as_dict=True,
	)


@frappe.whitelist()
def maak_map(ruimte: str | None = None, naam: str = "", bovenliggende: str | None = None, deling: str | None = None) -> dict:
	"""Maak een map (of geef de bestaande met dezelfde naam op die plek terug). Met `deling` is de gedeelde map het
	hoofdniveau (`bovenliggende` leeg)."""
	ruimte, wortel = _wortel(ruimte, deling)
	bovenliggende = _controleer_map(ruimte, bovenliggende or wortel)
	_binnen(wortel, bovenliggende)
	map, nieuw = _maak_map(ruimte, naam, bovenliggende)
	return {"map": map, "nieuw": nieuw}


def _maak_map(ruimte: str, naam: str, bovenliggende: str | None) -> tuple[str, bool]:
	"""Map `naam` in `bovenliggende` (al gecontroleerd): de bestaande, of een nieuwe. Geeft (map, nieuw)."""
	vereis_op_plek(ruimte, bovenliggende, "write")
	naam = (naam or "").strip()
	# Gelijktijdige aanvragen (parallelle uploads die dezelfde map nodig hebben) wachten op elkaar via een slot op de
	# ruimte; de controle daarna is ook een vergrendelende leesactie, zodat hij de map ziet die een andere aanvraag
	# net aanmaakte (een gewone select leest uit de momentopname van het begin van de transactie).
	frappe.db.sql("select name from `tabRuimte` where name = %s for update", ruimte)
	bestaand = frappe.db.sql(
		"""select name from `tabOpslagmap` where ruimte = %s and ifnull(bovenliggende, '') = %s
		and lower(naam) = lower(%s) and verwijderd_op is null for update""",
		(ruimte, bovenliggende or "", naam),
	)
	if bestaand:
		# Een bestaande map mag je altijd opvragen, ook een met een naam van vóór de naamregels.
		return bestaand[0][0], False
	naam = _controleer_naam(naam)
	mapsjablonen.controleer_schrijven(ruimte, bovenliggende)
	doc = frappe.get_doc(
		{"doctype": "Opslagmap", "ruimte": ruimte, "naam": naam, "bovenliggende": bovenliggende}
	).insert(ignore_permissions=True)
	_log(ruimte, "nieuw", "Opslagmap", doc.name, naam)
	return doc.name, True


# --- veel kleine bestanden in één verzoek (sync van mappen met duizenden kleine bestanden) -------------------
#
# Per bestand gelden dezelfde regels als bij `upload_start`/`upload_klaar` en `download`: rechten, quotum (pool en
# ruimte), mapregels, gearchiveerde ruimtes, versies en conflicten, de wijzigingenlijst. Het antwoord heeft per
# bestand `ok` met het resultaat, of de fout met de foutcode; een fout bij één bestand houdt de andere niet op.

KLEIN_MAX_BESTAND = 1024 * 1024  # bytes per bestand
KLEIN_MAX_AANTAL = 64  # bestanden per verzoek
KLEIN_MAX_BYTES = 16 * 1024 * 1024  # bytes per verzoek
KLEIN_MAX_MAPPEN = 1000  # mappen per verzoek (maak_mappen)


@frappe.whitelist()
def mogelijkheden() -> dict:
	"""Wat deze server kent, zodat de desktop-app nieuwe wegen alleen gebruikt waar ze bestaan (een oudere server
	kent deze methode niet; dan blijft de app bij `bladeren` en uploads per bestand). `deling`: de parameter `deling`
	(een gedeelde map als bovenste map) bestaat."""
	grenzen = {"max_bestand": KLEIN_MAX_BESTAND, "max_aantal": KLEIN_MAX_AANTAL, "max_bytes": KLEIN_MAX_BYTES}
	return {"upload_klein": grenzen, "download_klein": grenzen, "boom": True, "maak_mappen": True, "deling": True}


@frappe.whitelist(methods=["POST"])
def maak_mappen(ruimte: str | None = None, paden=None, deling: str | None = None) -> dict:
	"""Veel mappen in één verzoek, voordat de bestanden erin komen: `paden` is een lijst mappaden met '/' (bijv.
	`Tekeningen/2026`); ontbrekende mappen, ook tussenliggende, worden aangemaakt zoals bij `maak_map`, bestaande
	worden hergebruikt (hoofdletters maken niet uit). Antwoord: `{mappen: {pad: map}, fouten: {pad: {fout,
	foutcode}}}`; een fout bij één pad houdt de andere niet op. Met `deling` zijn de paden binnen de gedeelde map."""
	paden = [p.strip("/") for p in _als_lijst(paden) if p and p.strip("/")]
	ruimte, wortel = _wortel(ruimte, deling)
	if not ruimte or not frappe.db.exists("Ruimte", ruimte):
		frappe.throw(_("Ruimte bestaat niet"), frappe.DoesNotExistError)
	if len(paden) > KLEIN_MAX_MAPPEN:
		weiger(_("Te veel mappen in één verzoek: hoogstens {0}").format(KLEIN_MAX_MAPPEN), "batch_te_groot")
	cache: dict = {}
	mappen, fouten = {}, {}
	for pad in sorted(set(paden), key=lambda p: (p.count("/"), p)):
		uit = _per_bestand(lambda pad=pad: {"map": _map_van_pad(ruimte, pad, cache, wortel)})
		if uit["ok"]:
			mappen[pad] = uit["map"]
		else:
			cache.clear()
			fouten[pad] = {"fout": uit["fout"], "foutcode": uit["foutcode"]}
	return {"mappen": mappen, "fouten": fouten}


@frappe.whitelist()
def boom(ruimte: str | None = None, start: int = 0, aantal: int = 10000, deling: str | None = None) -> dict:
	"""Alle mappen en bestanden van een ruimte in één keer (in plaats van `bladeren` per map), voor de sync. Mappen
	allemaal; bestanden per pagina (`start`, `aantal`, hoogstens 50 000), met `meer` als er nog een pagina is. Zonder
	`deling` alleen voor leden van de ruimte; met `deling` alles onder de gedeelde map (`wortel`, zelf niet in
	`mappen`; de mappen direct eronder hebben hem als `bovenliggende`)."""
	ruimte, wortel = _wortel(ruimte, deling)
	start, aantal = max(cint(start), 0), min(max(cint(aantal), 1), 50000)
	if wortel:
		vereis_op_plek(ruimte, wortel, "read")
		binnen = _submappen(wortel)
		filter_map = {"name": ("in", [m for m in binnen if m != wortel] or [""])}
		filter_bestand = {"map": ("in", binnen)}
	else:
		vereis(ruimte, "read")
		filter_map, filter_bestand = {}, {}
	mappen = frappe.get_all(
		"Opslagmap",
		filters={"ruimte": ruimte, "verwijderd_op": ("is", "not set"), **filter_map},
		fields=["name", "naam", "bovenliggende", "modified"],
		order_by="name asc",
		ignore_permissions=True,
	)
	bestanden = frappe.get_all(
		"Opslagbestand",
		filters={"ruimte": ruimte, "verwijderd_op": ("is", "not set"), **filter_bestand},
		fields=["name", "naam", "map", "grootte", "huidige_versie", "modified"],
		order_by="name asc",
		limit_start=start,
		limit_page_length=aantal + 1,
		ignore_permissions=True,
	)
	uit = {"mappen": mappen, "bestanden": bestanden[:aantal], "meer": len(bestanden) > aantal}
	return {**uit, "wortel": wortel} if wortel else uit


def _als_lijst(waarde) -> list:
	if isinstance(waarde, str):
		waarde = json.loads(waarde)
	return list(waarde or [])


def _per_bestand(functie, *args) -> dict:
	"""Voer `functie` uit voor één bestand van een batch: een weigering wordt het antwoord voor dat bestand (met de
	foutcode), zonder dat de batch stopt. Wat het bestand al in de database schreef, wordt teruggedraaid."""
	frappe.db.savepoint("klein_bestand")
	frappe.local.response.pop("foutcode", None)
	try:
		resultaat = functie(*args)
	except Exception as fout:
		try:
			frappe.db.rollback(save_point="klein_bestand")
		except Exception:
			# De hele transactie is al weg (bijv. door een gelijktijdige wijziging): dan mislukt het hele verzoek en
			# probeert de app elk bestand apart.
			raise fout from None
		foutcode = frappe.local.response.pop("foutcode", None)
		frappe.clear_messages()
		if not isinstance(fout, frappe.ValidationError | frappe.PermissionError | frappe.DoesNotExistError):
			frappe.log_error(title="openaec_storage: bestand in batch mislukt")
		return {"ok": False, "fout": str(fout) or fout.__class__.__name__, "foutcode": foutcode}
	return {"ok": True, **resultaat}


def _map_van_pad(ruimte: str, pad: str, mappen: dict, wortel: str | None = None) -> str | None:
	"""Map-id voor een mappad met '/' (leeg = hoofdniveau, of de wortel van een deling); ontbrekende mappen worden
	aangemaakt zoals `maak_map`."""
	bovenliggende, gedaan = wortel, []
	for deel in [d for d in (pad or "").split("/") if d]:
		gedaan.append(deel)
		sleutel = "/".join(gedaan).lower()
		if sleutel not in mappen:
			# Eerst zonder slot kijken: alleen een map die er nog niet is, vraagt het slot op de ruimte (anders
			# wachten alle verzoeken met kleine bestanden in dezelfde ruimte op elkaar).
			bestaand = _actieve_map(ruimte, bovenliggende, deel.strip())
			mappen[sleutel] = bestaand[0][0] if bestaand else _maak_map(ruimte, deel, bovenliggende)[0]
		bovenliggende = mappen[sleutel]
	return bovenliggende


def _upload_een(
	ruimte: str, item: dict, inhoud: bytes | None, bron: str, mappen: dict, wortel: str | None = None
) -> dict:
	if inhoud is None:
		frappe.throw(_("De inhoud van dit bestand ontbreekt in het verzoek"))
	grootte = cint(item.get("grootte"))
	if grootte != len(inhoud):
		frappe.throw(_("Ontvangen grootte wijkt af van de aangekondigde grootte"))
	if item.get("sha256") and hashlib.sha256(inhoud).hexdigest() != item["sha256"].lower():
		frappe.throw(_("Ontvangen inhoud klopt niet met de SHA-256"))
	if grootte > KLEIN_MAX_BESTAND:
		weiger(_("Dit bestand is te groot om samen met andere te uploaden"), "te_groot_voor_batch")
	if item.get("map"):
		map = _controleer_map(ruimte, item["map"])
		_binnen(wortel, map)
	else:
		map = _map_van_pad(ruimte, item.get("pad") or "", mappen, wortel)
	vereis_op_plek(ruimte, map, "write", _bestaand_id(ruimte, map, item.get("naam")))
	naam = _controleer_naam(item.get("naam"), ruimte, map)
	mapsjablonen.controleer_upload(ruimte, map, naam)
	_controleer_quotum(grootte, ruimte)
	basis_versie = item.get("basis_versie")
	sleutel = s3.nieuwe_sleutel()
	s3.zet(sleutel, inhoud)
	try:
		resultaat = _registreer(
			{
				"ruimte": ruimte,
				"map": map,
				"naam": naam,
				"grootte": grootte,
				"basis_versie": None if basis_versie in (None, "") else cint(basis_versie),
				"sha256": item.get("sha256"),
				"bron": bron if bron in BRONNEN else "api",
				"sleutel": sleutel,
				"blokgrootte": s3.blokgrootte(grootte),
				"blokhashes": [item["blokhash"]] if item.get("blokhash") else None,
			}
		)
	except Exception:
		s3.verwijder(sleutel)
		raise
	return {**resultaat, "map": map}


def upload_klein_bytes(
	ruimte: str | None, bestanden, inhoud: dict[str, bytes], bron: str = "sync", deling: str | None = None
) -> dict:
	"""Kern van `upload_klein`, met de inhoud per veldnaam (zo ook te gebruiken zonder HTTP-verzoek)."""
	bestanden = _als_lijst(bestanden)
	ruimte, wortel = _wortel(ruimte, deling)
	if not ruimte or not frappe.db.exists("Ruimte", ruimte):
		frappe.throw(_("Ruimte bestaat niet"), frappe.DoesNotExistError)
	if len(bestanden) > KLEIN_MAX_AANTAL or sum(len(b) for b in inhoud.values()) > KLEIN_MAX_BYTES:
		weiger(
			_("Te veel in één verzoek: hoogstens {0} bestanden en {1} MiB").format(
				KLEIN_MAX_AANTAL, KLEIN_MAX_BYTES // (1024 * 1024)
			),
			"batch_te_groot",
		)
	mappen: dict = {}
	resultaten = []
	for index, item in enumerate(bestanden):
		veld = item.get("veld") or f"f{index}"
		uit = _per_bestand(_upload_een, ruimte, item, inhoud.get(veld), bron, mappen, wortel)
		if not uit["ok"]:
			# Wat dit bestand aan mappen aanmaakte, is teruggedraaid: de volgende zoekt ze opnieuw op.
			mappen.clear()
		resultaten.append({"index": index, **uit})
	return {"resultaten": resultaten}


@frappe.whitelist(methods=["POST"])
def upload_klein(ruimte: str | None = None, bestanden=None, bron: str = "sync", deling: str | None = None) -> dict:
	"""Veel kleine bestanden in één verzoek (multipart/form-data): `ruimte`, `bestanden` (JSON-lijst met per bestand
	`naam`, `map` (id) of `pad` (mappad met '/'; ontbrekende mappen worden aangemaakt), `grootte`, `blokhash`
	(BLAKE3 van de inhoud), `basis_versie?`, `sha256?` en `veld`) en de inhoud: alle bestanden achter elkaar in één
	bestandsveld `inhoud` (in de volgorde van `bestanden`, gesneden op `grootte`), of per bestand een bestandsveld
	met de naam uit `veld` (standaard `f<index>`). Met `sha256` wordt de ontvangen inhoud gecontroleerd. Hoogstens
	1 MiB per bestand en 64 bestanden of 16 MiB per verzoek. Antwoord: `{resultaten: [{index, ok: true, bestand, naam,
	versie, conflict, map} | {index, ok: false, fout, foutcode}]}`, in de volgorde van `bestanden`. Met `deling` zijn
	`pad` en `map` binnen de gedeelde map."""
	bestanden = _als_lijst(bestanden)
	velden = frappe.request.files if getattr(frappe, "request", None) else {}
	inhoud = {}
	if "inhoud" in velden:
		# Eén deel met alles achter elkaar: het einde van een deel is in multipart het lastigste stuk (een
		# regeleinde kan aan de inhoud blijven hangen), dus zo telt alleen `grootte`.
		alles, positie = velden["inhoud"].stream.read(KLEIN_MAX_BYTES + 1024), 0
		for index, item in enumerate(bestanden):
			lengte = max(cint(item.get("grootte")), 0)
			if positie + lengte <= len(alles):
				inhoud[item.get("veld") or f"f{index}"] = alles[positie : positie + lengte]
			positie += lengte
	else:
		for index, item in enumerate(bestanden):
			veld = item.get("veld") or f"f{index}"
			if veld in velden:
				inhoud[veld] = velden[veld].stream.read(KLEIN_MAX_BYTES + 1)
	return upload_klein_bytes(ruimte, bestanden, inhoud, bron, deling)


def _download_een(item) -> dict:
	bestand = item.get("bestand") if isinstance(item, dict) else item
	versie = item.get("versie") if isinstance(item, dict) else None
	doc = _bestand(bestand, "read")
	volgnummer = cint(versie) or cint(doc.huidige_versie)
	rij = frappe.db.get_value(
		"Bestandsversie",
		{"bestand": bestand, "volgnummer": volgnummer},
		["s3_sleutel", "grootte", "blokhashes"],
		as_dict=True,
	)
	if not rij:
		frappe.throw(_("Versie bestaat niet"), frappe.DoesNotExistError)
	if cint(rij.grootte) > KLEIN_MAX_BESTAND:
		weiger(_("Dit bestand is te groot om samen met andere te downloaden"), "te_groot_voor_batch")
	hashes = json.loads(rij.blokhashes) if rij.blokhashes else None
	return {
		"bestand": doc.name,
		"naam": doc.naam,
		"versie": volgnummer,
		"grootte": cint(rij.grootte),
		"blokhash": hashes[0] if hashes else None,
		"sleutel": rij.s3_sleutel,
	}


@frappe.whitelist()
def download_klein(bestanden) -> dict:
	"""Veel kleine bestanden in één antwoord: `bestanden` is een lijst bestand-id's (of `{bestand, versie}`).
	Hoogstens 1 MiB per bestand en 64 bestanden of 16 MiB per verzoek; wat er niet meer bij past, krijgt de foutcode
	`batch_te_groot` (vraag het in een volgend verzoek). Antwoord: `{bestanden: [{index, ok: true, bestand, naam,
	versie, grootte, blokhash, inhoud (base64)} | {index, ok: false, fout, foutcode}]}`, in de volgorde van
	`bestanden`."""
	import base64

	bestanden = _als_lijst(bestanden)
	if len(bestanden) > KLEIN_MAX_AANTAL:
		weiger(_("Te veel in één verzoek: hoogstens {0} bestanden").format(KLEIN_MAX_AANTAL), "batch_te_groot")
	uit, totaal = [], 0
	for index, item in enumerate(bestanden):
		r = _per_bestand(_download_een, item)
		if r["ok"]:
			totaal += r["grootte"]
			if totaal > KLEIN_MAX_BYTES:
				r = {"ok": False, "fout": _("Past niet meer in dit verzoek"), "foutcode": "batch_te_groot"}
		uit.append({"index": index, **r})
	# De inhoud tegelijk uit S3 halen: anders wacht één verzoek met 64 bestanden 64 keer na elkaar op S3.
	klaar = [r for r in uit if r["ok"]]
	for r, data in zip(klaar, s3.lees_veel([r.pop("sleutel") for r in klaar]), strict=True):
		if isinstance(data, Exception):
			index = r["index"]
			r.clear()
			r.update({"index": index, "ok": False, "fout": str(data), "foutcode": None})
		else:
			r["inhoud"] = base64.b64encode(data).decode()
	return {"bestanden": uit}
