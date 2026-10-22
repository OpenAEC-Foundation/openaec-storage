"""Overdrachten: losse bestanden versturen via een link (en eventueel e-mail), ook bestanden die niet in een ruimte
staan. Serverkant van "Versturen met SuperCloud Storage" in de desktop-app.

Verloop voor de app (zie README, "Overdrachten"):
1. `maak` geeft een concept-overdracht;
2. per bestand `bestand_start` (ondertekende links per deel, zoals bij gewone uploads) en `bestand_klaar`;
   valt de verbinding weg, dan zegt `bestand_stand` welke delen S3 al heeft en geeft links voor de rest;
3. `voltooi` maakt de link `/overdracht/<token>` en mailt eventuele ontvangers.

De bytes staan in S3 onder `<prefix>/overdrachten/<overdracht>/`, niet in een ruimte: ze vervuilen geen ruimte van
de gebruiker en zijn in één keer op te ruimen. Naam en bericht zijn platte tekst (zonder HTML-filter opgeslagen); ze
worden overal ge-escaped getoond. Zolang een overdracht bestaat (concept of actief) telt hij mee in het
opslaggebruik van de omgeving. `opruimen` (elk uur) haalt verlopen overdrachten en concepten die langer dan
`openaec_storage_overdracht_concept_uren` (standaard 24) blijven liggen weg: objecten uit S3, record naar Verlopen.

"Alles downloaden" is een zip die de server streamt (zonder compressie, zonder hem eerst op te slaan), tot
`openaec_storage_overdracht_zip_max_gb` (standaard 2 GB). Daarboven haalt de pagina de bestanden een voor een op
met ondertekende links rechtstreeks uit S3: een grote zip houdt een werker van de server lang bezig en breekt af als
de verbinding hapert, terwijl een losse download uit S3 kan hervatten.
"""

import json
import math
import os
import re
import zipfile
from io import RawIOBase
from urllib.parse import quote

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import (
	add_to_date,
	cint,
	escape_html,
	flt,
	format_datetime,
	get_datetime,
	get_fullname,
	get_url,
	now_datetime,
	validate_email_address,
)

from openaec_storage import s3
from openaec_storage.rechten import is_superbeheer, mag_overdragen

STANDAARD_MAX_GB = 100
STANDAARD_DAGEN = 7
STANDAARD_MAX_DAGEN = 30
STANDAARD_MAX_ONTVANGERS = 20
STANDAARD_CONCEPT_UREN = 24
STANDAARD_ZIP_MAX_GB = 2

# Statussen waarin de bestanden nog in S3 staan (en dus meetellen in het opslaggebruik).
BESTAAND = ("Concept", "Actief")
DATUM = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --- grenzen (site_config) ---------------------------------------------------------------------------


def _max_bytes() -> int:
	return int(flt(frappe.conf.get("openaec_storage_overdracht_max_gb") or STANDAARD_MAX_GB) * 1000**3)


def _max_dagen() -> int:
	return cint(frappe.conf.get("openaec_storage_overdracht_max_dagen")) or STANDAARD_MAX_DAGEN


def _max_ontvangers() -> int:
	return cint(frappe.conf.get("openaec_storage_overdracht_max_ontvangers")) or STANDAARD_MAX_ONTVANGERS


def _concept_uren() -> int:
	return cint(frappe.conf.get("openaec_storage_overdracht_concept_uren")) or STANDAARD_CONCEPT_UREN


def _zip_max_bytes() -> int:
	return int(flt(frappe.conf.get("openaec_storage_overdracht_zip_max_gb") or STANDAARD_ZIP_MAX_GB) * 1000**3)


def gebruikt_bytes() -> int:
	"""Bytes in overdrachten die nog bestaan (concept of actief); telt mee in het opslaggebruik van de omgeving."""
	return cint(
		frappe.db.sql(
			"""select coalesce(sum(b.grootte), 0) from `tabOverdrachtbestand` b
			join `tabOverdracht` o on o.name = b.overdracht where o.status in %s""",
			(BESTAAND,),
		)[0][0]
	)


@frappe.whitelist()
def grenzen() -> dict:
	"""De grenzen voor het venster in de app (bijv. "2 MB van 100 GB") en het opslaggebruik van de omgeving."""
	from openaec_storage.api import quotum

	_vereis_recht()
	return {
		"max_bytes": _max_bytes(),
		"standaard_dagen": STANDAARD_DAGEN,
		"max_dagen": _max_dagen(),
		"max_ontvangers": _max_ontvangers(),
		"concept_uren": _concept_uren(),
		"zip_max_bytes": _zip_max_bytes(),
		"quotum": quotum(),
	}


# --- hulpfuncties ------------------------------------------------------------------------------------


def _vereis_recht() -> None:
	if not mag_overdragen():
		frappe.throw(_("Je hebt geen recht om bestanden te versturen"), frappe.PermissionError)


def _overdracht(naam: str, status: str | None = None):
	"""De overdracht, alleen voor de maker (of het beheer); met `status` moet hij in die status zijn."""
	doc = frappe.db.get_value(
		"Overdracht",
		naam or "",
		["name", "naam", "status", "owner", "bericht", "ontvangers", "verloopt_op", "token", "wachtwoord_hash",
			"totale_grootte", "aantal_bestanden", "aantal_downloads", "laatst_gedownload", "creation", "voltooid_op"],
		as_dict=True,
	)
	if not doc:
		frappe.throw(_("Overdracht bestaat niet"), frappe.DoesNotExistError)
	if doc.owner != frappe.session.user and not is_superbeheer():
		frappe.throw(_("Dit is niet jouw overdracht"), frappe.PermissionError)
	if status and doc.status != status:
		frappe.throw(_("Deze overdracht is al {0}").format(_(doc.status).lower()))
	return doc


def _bestand(overdracht: str, bestand: str):
	rij = frappe.db.get_value(
		"Overdrachtbestand",
		{"name": bestand or "", "overdracht": overdracht},
		["name", "naam", "grootte", "s3_sleutel", "upload_id", "deelgrootte", "klaar", "sha256"],
		as_dict=True,
	)
	if not rij:
		frappe.throw(_("Bestand hoort niet bij deze overdracht"), frappe.DoesNotExistError)
	return rij


def _verloopt_op(waarde):
	"""Vervaldatum: leeg = over 7 dagen; een datum (JJJJ-MM-DD) geldt tot het eind van die dag; hoogstens
	`openaec_storage_overdracht_max_dagen` dagen vooruit."""
	nu = now_datetime()
	if not waarde:
		return add_to_date(nu, days=STANDAARD_DAGEN)
	waarde = str(waarde).strip()
	try:
		moment = get_datetime(waarde + " 23:59:59" if DATUM.match(waarde) else waarde)
	except Exception:
		frappe.throw(_("Ongeldige vervaldatum"))
	if moment <= nu:
		frappe.throw(_("De vervaldatum ligt in het verleden"))
	if moment > add_to_date(nu, days=_max_dagen()):
		frappe.throw(_("Een overdracht kan hoogstens {0} dagen geldig zijn").format(_max_dagen()))
	return moment


def _ontvangers(waarde) -> list[str]:
	"""Lijst of tekst (komma's, puntkomma's of regels); zonder dubbelen, elk adres nagekeken, hoogstens 20."""
	if not waarde:
		return []
	if isinstance(waarde, str):
		try:
			waarde = json.loads(waarde) if waarde.strip().startswith("[") else re.split(r"[,;\s]+", waarde)
		except ValueError:
			frappe.throw(_("Ongeldige lijst ontvangers"))
	uit, gezien = [], set()
	for adres in waarde:
		adres = (adres or "").strip()
		if not adres or adres.lower() in gezien:
			continue
		if not validate_email_address(adres):
			frappe.throw(_("Ongeldig e-mailadres: {0}").format(escape_html(adres)))
		gezien.add(adres.lower())
		uit.append(adres)
	if len(uit) > _max_ontvangers():
		frappe.throw(_("Hoogstens {0} ontvangers per overdracht").format(_max_ontvangers()))
	return uit


def _vrije_naam(overdracht: str, naam: str) -> str:
	stam, ext = os.path.splitext(naam)
	kandidaat, teller = naam, 2
	while frappe.db.exists("Overdrachtbestand", {"overdracht": overdracht, "naam": kandidaat}):
		kandidaat = f"{stam} ({teller}){ext}"
		teller += 1
	return kandidaat


def _url(token: str) -> str:
	return get_url(f"/overdracht/{token}")


def _tel_bij(overdracht: str) -> None:
	"""Totale grootte en aantal bestanden opnieuw uit de bestanden halen."""
	totaal, aantal = frappe.db.sql(
		"select coalesce(sum(grootte), 0), count(*) from `tabOverdrachtbestand` where overdracht = %s", overdracht
	)[0]
	frappe.db.set_value(
		"Overdracht", overdracht, {"totale_grootte": cint(totaal), "aantal_bestanden": cint(aantal)}, update_modified=False
	)


def _bestand_uit(rij) -> dict:
	return {"bestand": rij.name, "naam": rij.naam, "grootte": cint(rij.grootte), "klaar": bool(cint(rij.klaar))}


# --- maken en uploaden (de app van de afzender) ------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def maak(
	naam: str,
	bericht: str | None = None,
	verloopt_op: str | None = None,
	wachtwoord: str | None = None,
	ontvangers=None,
) -> dict:
	"""Begin een overdracht (concept). Daarna per bestand `bestand_start`/`bestand_klaar` en tot slot `voltooi`."""
	from frappe.utils.password import passlibctx

	_vereis_recht()
	naam = (naam or "").strip()[:140]
	if not naam:
		frappe.throw(_("Geef de overdracht een naam"))
	doc = frappe.get_doc(
		{
			"doctype": "Overdracht",
			"naam": naam,
			"status": "Concept",
			"bericht": (bericht or "").strip()[:5000] or None,
			"ontvangers": "\n".join(_ontvangers(ontvangers)) or None,
			"verloopt_op": _verloopt_op(verloopt_op),
			"wachtwoord_hash": passlibctx.hash(wachtwoord) if wachtwoord else None,
		}
	).insert(ignore_permissions=True)
	return {
		"overdracht": doc.name,
		"naam": doc.naam,
		"status": doc.status,
		"verloopt_op": doc.verloopt_op,
		"max_bytes": _max_bytes(),
		"concept_geldig_tot": add_to_date(doc.creation, hours=_concept_uren()),
	}


@frappe.whitelist(methods=["POST"])
def bestand_start(overdracht: str, naam: str, grootte: int, sha256: str | None = None) -> dict:
	"""Voeg een bestand toe aan een concept-overdracht: per deel een ondertekende upload-URL (PUT), zoals bij gewone
	uploads. De multipart-upload staat bij het bestand, zodat hij na een onderbreking verder kan (`bestand_stand`)."""
	from openaec_storage.api import _controleer_naam, _controleer_quotum

	_vereis_recht()
	doc = _overdracht(overdracht, "Concept")
	naam = _controleer_naam(naam)
	grootte = cint(grootte)
	if grootte < 0:
		frappe.throw(_("Ongeldige grootte"))
	# Gelijktijdige aanvragen voor dezelfde overdracht na elkaar: zo klopt de totale grootte en blijven namen uniek.
	frappe.db.sql("select name from `tabOverdracht` where name = %s for update", doc.name)
	al = cint(
		frappe.db.sql("select coalesce(sum(grootte), 0) from `tabOverdrachtbestand` where overdracht = %s", doc.name)[0][0]
	)
	if al + grootte > _max_bytes():
		frappe.throw(_("Een overdracht kan hoogstens {0} groot zijn").format(_leesbaar(_max_bytes())))
	_controleer_quotum(grootte)

	blok = s3.blokgrootte(grootte)
	aantal = max(1, math.ceil(grootte / blok))
	sleutel = s3.overdracht_sleutel(doc.name)
	upload_id = s3.start_multipart(sleutel)
	rij = frappe.get_doc(
		{
			"doctype": "Overdrachtbestand",
			"overdracht": doc.name,
			"naam": _vrije_naam(doc.name, naam),
			"grootte": grootte,
			"sha256": sha256,
			"s3_sleutel": sleutel,
			"upload_id": upload_id,
			"deelgrootte": blok,
		}
	).insert(ignore_permissions=True)
	_tel_bij(doc.name)
	return {
		"bestand": rij.name,
		"naam": rij.naam,
		"grootte": grootte,
		"deelgrootte": blok,
		"delen": [{"nummer": n, "url": s3.deel_url(sleutel, upload_id, n)} for n in range(1, aantal + 1)],
	}


def _leesbaar(aantal: int) -> str:
	for eenheid in ("B", "kB", "MB", "GB", "TB"):
		if aantal < 1000 or eenheid == "TB":
			return f"{aantal:.0f} {eenheid}" if eenheid == "B" else f"{aantal:.1f} {eenheid}".replace(".0 ", " ")
		aantal /= 1000
	return str(aantal)


def _verwacht(rij) -> int:
	return max(1, math.ceil(cint(rij.grootte) / (cint(rij.deelgrootte) or s3.DEELGROOTTE)))


@frappe.whitelist()
def bestand_stand(overdracht: str, bestand: str) -> dict:
	"""Hervatten: welke delen S3 al heeft (`aanwezig`), welke nog ontbreken (`ontbrekend`) en voor die delen
	nieuwe ondertekende upload-URL's (`delen`). Een deel telt als aanwezig als S3 het volledig heeft."""
	_vereis_recht()
	doc = _overdracht(overdracht)
	rij = _bestand(doc.name, bestand)
	uit = {**_bestand_uit(rij), "deelgrootte": cint(rij.deelgrootte)}
	if cint(rij.klaar):
		return {**uit, "aanwezig": [], "ontbrekend": [], "delen": []}
	if doc.status != "Concept":
		frappe.throw(_("Deze overdracht is al {0}").format(_(doc.status).lower()))
	aantal, blok = _verwacht(rij), cint(rij.deelgrootte)
	aanwezig = []
	for deel in s3.aanwezige_delen(rij.s3_sleutel, rij.upload_id):
		n = deel["nummer"]
		verwacht = max(min(blok, cint(rij.grootte) - (n - 1) * blok), 0)
		if 1 <= n <= aantal and deel["grootte"] == verwacht:
			aanwezig.append(deel)
	nummers = {d["nummer"] for d in aanwezig}
	ontbrekend = [n for n in range(1, aantal + 1) if n not in nummers]
	return {
		**uit,
		"aanwezig": aanwezig,
		"ontbrekend": ontbrekend,
		"delen": [{"nummer": n, "url": s3.deel_url(rij.s3_sleutel, rij.upload_id, n)} for n in ontbrekend],
	}


@frappe.whitelist(methods=["POST"])
def bestand_klaar(overdracht: str, bestand: str, delen=None) -> dict:
	"""Rond de upload van één bestand af. `delen` ([{nummer, etag}]) mag weg: dan vraagt de server de delen zelf
	aan S3 (handig na een hervatte upload). Nog een keer aanroepen op een afgerond bestand kan geen kwaad."""
	_vereis_recht()
	doc = _overdracht(overdracht)
	rij = _bestand(doc.name, bestand)
	if cint(rij.klaar):
		return _bestand_uit(rij)
	if doc.status != "Concept":
		frappe.throw(_("Deze overdracht is al {0}").format(_(doc.status).lower()))
	stand = bestand_stand(overdracht=doc.name, bestand=rij.name)
	if stand["ontbrekend"]:
		frappe.throw(
			_("Nog niet alle delen zijn ontvangen (ontbrekend: {0})").format(", ".join(map(str, stand["ontbrekend"])))
		)
	if isinstance(delen, str):
		delen = json.loads(delen)
	etags = {d["nummer"]: d["etag"] for d in stand["aanwezig"]}
	if delen:
		# De app kent de ETags zelf ook; S3 controleert ze bij het afronden.
		etags.update({cint(d["nummer"]): d["etag"] for d in delen if cint(d.get("nummer")) in etags})
	s3.voltooi(rij.s3_sleutel, rij.upload_id, [{"nummer": n, "etag": e} for n, e in etags.items()])
	if s3.grootte(rij.s3_sleutel) != cint(rij.grootte):
		frappe.throw(_("Ontvangen grootte wijkt af van de aangekondigde grootte"))
	frappe.db.set_value("Overdrachtbestand", rij.name, {"klaar": 1, "upload_id": None})
	rij.klaar = 1
	return _bestand_uit(rij)


@frappe.whitelist(methods=["POST"])
def bestand_weg(overdracht: str, bestand: str) -> None:
	"""Haal een bestand uit een concept-overdracht (voor het versturen)."""
	_vereis_recht()
	doc = _overdracht(overdracht, "Concept")
	rij = _bestand(doc.name, bestand)
	try:
		if cint(rij.klaar):
			s3.verwijder(rij.s3_sleutel)
		elif rij.upload_id:
			s3.afbreken(rij.s3_sleutel, rij.upload_id)
	except Exception:
		# Wat hier blijft staan, ruimt `opruimen` met de hele overdracht op.
		frappe.log_error(title="Bestand uit overdracht niet uit S3 verwijderd")
	frappe.db.delete("Overdrachtbestand", {"name": rij.name})
	_tel_bij(doc.name)


@frappe.whitelist(methods=["POST"])
def voltooi(overdracht: str) -> dict:
	"""Maak de link (en mail de ontvangers). Kan pas als elk bestand afgerond is (`bestand_klaar`)."""
	_vereis_recht()
	doc = _overdracht(overdracht, "Concept")
	frappe.db.sql("select name from `tabOverdracht` where name = %s for update", doc.name)
	rijen = frappe.get_all(
		"Overdrachtbestand",
		filters={"overdracht": doc.name},
		fields=["name", "naam", "grootte", "klaar"],
		order_by="naam asc",
	)
	if not rijen:
		frappe.throw(_("Voeg eerst een of meer bestanden toe"))
	open_ = [r.naam for r in rijen if not cint(r.klaar)]
	if open_:
		frappe.throw(_("Nog niet alle bestanden zijn geüpload: {0}").format(escape_html(", ".join(open_))))
	if get_datetime(doc.verloopt_op) <= now_datetime():
		frappe.throw(_("De vervaldatum ligt in het verleden"))
	token = frappe.generate_hash(length=40)
	_tel_bij(doc.name)
	frappe.db.set_value(
		"Overdracht", doc.name, {"status": "Actief", "token": token, "voltooid_op": now_datetime()}
	)
	ontvangers = [a for a in (doc.ontvangers or "").splitlines() if a.strip()]
	gemaild = _mail(doc, token, rijen, ontvangers) if ontvangers else []
	return {
		"overdracht": doc.name,
		"status": "Actief",
		"token": token,
		"url": _url(token),
		"verloopt_op": doc.verloopt_op,
		"aantal_bestanden": len(rijen),
		"totale_grootte": sum(cint(r.grootte) for r in rijen),
		"mail": gemaild,
	}


def _mail(doc, token: str, rijen, ontvangers: list[str]) -> list[str]:
	"""Mail via de site (Frappe-mail, in de wachtrij) met link, bericht en bestanden. Het wachtwoord gaat nooit mee."""
	afzender = get_fullname(doc.owner) or doc.owner
	url = _url(token)
	bestanden = "".join(
		f"<li>{escape_html(r.naam)} <span style='color:#6b6b76'>({_leesbaar(cint(r.grootte))})</span></li>" for r in rijen
	)
	bericht = (
		f"<p style='white-space:pre-line;border-left:3px solid #d97706;padding-left:12px'>{escape_html(doc.bericht)}</p>"
		if doc.bericht
		else ""
	)
	beveiligd = (
		"<p>" + _("De link is beveiligd met een wachtwoord; dat krijg je los van deze mail van de afzender.") + "</p>"
		if doc.wachtwoord_hash
		else ""
	)
	html = (
		f"<p>{escape_html(_('{0} heeft je bestanden gestuurd via OpenAEC SuperCloud.').format(afzender))}</p>"
		f"<h3>{escape_html(doc.naam)}</h3>{bericht}<ul>{bestanden}</ul>"
		f"<p><a href='{escape_html(url)}' style='display:inline-block;background:#d97706;color:#fff;padding:10px 18px;"
		f"border-radius:6px;text-decoration:none'>{escape_html(_('Bestanden downloaden'))}</a></p>"
		f"<p>{escape_html(url)}</p>{beveiligd}"
		f"<p style='color:#6b6b76'>{escape_html(_('Beschikbaar tot {0}.').format(format_datetime(doc.verloopt_op, 'd MMMM yyyy HH:mm')))}</p>"
	)
	try:
		frappe.sendmail(
			recipients=ontvangers,
			subject=_("{0} heeft je bestanden gestuurd: {1}").format(afzender, doc.naam),
			message=html,
			reply_to=doc.owner,
			reference_doctype="Overdracht",
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title="Mail over een overdracht niet verstuurd")
		return []
	return ontvangers


@frappe.whitelist()
def mijn() -> list[dict]:
	"""Eigen overdrachten, nieuwste eerst: status, downloads, vervaldatum en link (alleen als hij actief is)."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Log in om je overdrachten te zien"), frappe.PermissionError)
	rijen = frappe.get_all(
		"Overdracht",
		filters={"owner": frappe.session.user},
		fields=["name", "naam", "status", "verloopt_op", "token", "totale_grootte", "aantal_bestanden",
			"aantal_downloads", "laatst_gedownload", "ontvangers", "wachtwoord_hash", "creation", "voltooid_op"],
		order_by="creation desc",
		limit=500,
		ignore_permissions=True,
	)
	return [
		{
			"overdracht": r.name,
			"naam": r.naam,
			"status": r.status,
			"verloopt_op": r.verloopt_op,
			"totale_grootte": cint(r.totale_grootte),
			"aantal_bestanden": cint(r.aantal_bestanden),
			"aantal_downloads": cint(r.aantal_downloads),
			"laatst_gedownload": r.laatst_gedownload,
			"ontvangers": [a for a in (r.ontvangers or "").splitlines() if a],
			"beveiligd": bool(r.wachtwoord_hash),
			"url": _url(r.token) if r.status == "Actief" and r.token else None,
			"gemaakt": r.creation,
			"voltooid_op": r.voltooid_op,
		}
		for r in rijen
	]


@frappe.whitelist()
def info(overdracht: str) -> dict:
	"""Eén eigen overdracht met zijn bestanden (en per bestand of het afgerond is): om een onderbroken overdracht
	in de app verder te zetten."""
	doc = _overdracht(overdracht)
	rijen = frappe.get_all(
		"Overdrachtbestand",
		filters={"overdracht": doc.name},
		fields=["name", "naam", "grootte", "klaar", "aantal_downloads"],
		order_by="creation asc",
	)
	return {
		"overdracht": doc.name,
		"naam": doc.naam,
		"status": doc.status,
		"bericht": doc.bericht,
		"ontvangers": [a for a in (doc.ontvangers or "").splitlines() if a],
		"verloopt_op": doc.verloopt_op,
		"beveiligd": bool(doc.wachtwoord_hash),
		"url": _url(doc.token) if doc.status == "Actief" and doc.token else None,
		"totale_grootte": sum(cint(r.grootte) for r in rijen),
		"aantal_downloads": cint(doc.aantal_downloads),
		"concept_geldig_tot": add_to_date(doc.creation, hours=_concept_uren()) if doc.status == "Concept" else None,
		"bestanden": [{**_bestand_uit(r), "aantal_downloads": cint(r.aantal_downloads)} for r in rijen],
	}


@frappe.whitelist(methods=["POST"])
def trek_in(overdracht: str) -> dict:
	"""Trek een overdracht in (ook een concept): de link werkt niet meer en de bestanden gaan meteen uit S3."""
	doc = _overdracht(overdracht)
	if doc.status in BESTAAND:
		_weg_uit_s3(doc.name)
		frappe.db.set_value("Overdracht", doc.name, "status", "Ingetrokken")
	return {"overdracht": doc.name, "status": "Ingetrokken" if doc.status in BESTAAND else doc.status}


def _weg_uit_s3(overdracht: str) -> None:
	s3.verwijder_map(s3.overdracht_map(overdracht))
	# Ook sleutels van voor een gewijzigd voorvoegsel (openaec_storage_prefix) staan bij de bestanden.
	for rij in frappe.get_all(
		"Overdrachtbestand", filters={"overdracht": overdracht}, fields=["s3_sleutel", "upload_id", "klaar"]
	):
		if rij.s3_sleutel and not rij.s3_sleutel.startswith(s3.overdracht_map(overdracht)):
			s3.verwijder_map(rij.s3_sleutel)
	frappe.db.set_value("Overdrachtbestand", {"overdracht": overdracht}, "upload_id", None)


def opruimen() -> None:
	"""Geplande taak (elk uur): verlopen overdrachten en blijven liggende concepten uit S3 en naar Verlopen."""
	nu = now_datetime()
	verlopen = frappe.get_all(
		"Overdracht", filters={"status": "Actief", "verloopt_op": ("<", nu)}, pluck="name"
	) + frappe.get_all(
		"Overdracht",
		filters={"status": "Concept", "creation": ("<", add_to_date(nu, hours=-_concept_uren()))},
		pluck="name",
	)
	for naam in verlopen:
		try:
			_weg_uit_s3(naam)
		except Exception:
			# Blijft op zijn status staan (en telt mee) tot het opruimen wel lukt.
			frappe.log_error(title=f"Overdracht {naam} niet opgeruimd")
			continue
		frappe.db.set_value("Overdracht", naam, "status", "Verlopen")


# --- ontvangen (gast, via de link) -------------------------------------------------------------------


def stand(token: str | None) -> tuple[str, dict | None]:
	"""Wat de ontvangstpagina toont: `geldig`, `wachtwoord`, `onbekend` (bestaat niet of nog een concept),
	`ingetrokken` of `verlopen`, plus de overdracht."""
	rij = (
		frappe.db.get_value(
			"Overdracht",
			{"token": token},
			["name", "naam", "status", "owner", "bericht", "verloopt_op", "wachtwoord_hash", "totale_grootte"],
			as_dict=True,
		)
		if token
		else None
	)
	if not rij or rij.status == "Concept":
		return "onbekend", None
	if rij.status == "Ingetrokken":
		return "ingetrokken", rij
	if rij.status == "Verlopen" or get_datetime(rij.verloopt_op) < now_datetime():
		return "verlopen", rij
	return ("wachtwoord" if rij.wachtwoord_hash else "geldig"), rij


def _geldig(token: str, wachtwoord: str | None):
	from frappe.utils.password import passlibctx

	toestand, rij = stand(token)
	if toestand not in ("geldig", "wachtwoord"):
		frappe.throw(_("Deze link is niet (meer) geldig"), frappe.PermissionError)
	if rij.wachtwoord_hash and not (wachtwoord and passlibctx.verify(wachtwoord, rij.wachtwoord_hash)):
		frappe.throw(_("Wachtwoord vereist of onjuist"), frappe.PermissionError)
	return rij


def _bestanden(overdracht: str):
	return frappe.get_all(
		"Overdrachtbestand",
		filters={"overdracht": overdracht, "klaar": 1},
		fields=["name", "naam", "grootte", "s3_sleutel"],
		order_by="naam asc",
		ignore_permissions=True,
	)


def _tel_download(overdracht: str, bestand: str | None = None) -> None:
	frappe.db.sql(
		"update `tabOverdracht` set aantal_downloads = aantal_downloads + 1, laatst_gedownload = %s where name = %s",
		(now_datetime(), overdracht),
	)
	if bestand:
		frappe.db.sql(
			"update `tabOverdrachtbestand` set aantal_downloads = aantal_downloads + 1 where name = %s", bestand
		)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=300, seconds=3600)
def openen(token: str, wachtwoord: str | None = None) -> dict:
	"""Wat er in de overdracht zit: naam, bericht, afzender, vervaldatum en de bestanden met grootte."""
	rij = _geldig(token, wachtwoord)
	bestanden = _bestanden(rij.name)
	totaal = sum(cint(b.grootte) for b in bestanden)
	afzender = rij.owner if rij.owner not in ("Guest", "Administrator") else None
	return {
		"naam": rij.naam,
		"bericht": rij.bericht,
		"afzender": get_fullname(afzender) if afzender else None,
		"verloopt_op": rij.verloopt_op,
		"totale_grootte": totaal,
		"zip": len(bestanden) > 0 and totaal <= _zip_max_bytes(),
		"bestanden": [{"bestand": b.name, "naam": b.naam, "grootte": cint(b.grootte)} for b in bestanden],
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=600, seconds=3600)
def download(token: str, bestand: str, wachtwoord: str | None = None) -> dict:
	"""Tijdelijke downloadlink (rechtstreeks uit S3) voor één bestand uit de overdracht; telt als download."""
	rij = _geldig(token, wachtwoord)
	b = frappe.db.get_value(
		"Overdrachtbestand", {"name": bestand or "", "overdracht": rij.name, "klaar": 1}, ["name", "naam", "s3_sleutel"],
		as_dict=True,
	)
	if not b:
		frappe.throw(_("Bestand niet gevonden"), frappe.DoesNotExistError)
	_tel_download(rij.name, b.name)
	return {"url": s3.download_url(b.s3_sleutel, b.naam), "geldig_seconden": s3.LINK_GELDIG_SECONDEN}


class _Stroom(RawIOBase):
	"""Schrijfbare stroom zonder terugspoelen: zipfile schrijft erin, de generator geeft het telkens door."""

	def __init__(self):
		self.stukken, self.positie = [], 0

	def writable(self):
		return True

	def write(self, b):
		self.stukken.append(bytes(b))
		self.positie += len(b)
		return len(b)

	def tell(self):
		return self.positie

	def neem(self) -> bytes:
		uit, self.stukken = b"".join(self.stukken), []
		return uit


def _zipstroom(bestanden: list[tuple[str, str, int]], lees):
	stroom = _Stroom()
	moment = now_datetime().timetuple()[:6]
	with zipfile.ZipFile(stroom, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as archief:
		for naam, sleutel, grootte in bestanden:
			info = zipfile.ZipInfo(naam, date_time=moment)
			info.compress_type = zipfile.ZIP_STORED
			with archief.open(info, mode="w", force_zip64=grootte > 2**31) as doel:
				for stuk in lees(sleutel):
					doel.write(stuk)
					yield stroom.neem()
			yield stroom.neem()
	yield stroom.neem()


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=60, seconds=3600)
def zip(token: str, wachtwoord: str | None = None):
	"""Alles downloaden als één zip, gestreamd door de server (zie de uitleg bovenaan); telt als één download."""
	from werkzeug.wrappers import Response

	rij = _geldig(token, wachtwoord)
	bestanden = _bestanden(rij.name)
	if not bestanden:
		frappe.throw(_("Deze overdracht heeft geen bestanden"), frappe.DoesNotExistError)
	if sum(cint(b.grootte) for b in bestanden) > _zip_max_bytes():
		frappe.throw(_("Te groot om als zip te downloaden; download de bestanden los"))
	# Frappe legt de teller vast voordat de stroom begint; die loopt zonder database (zie s3.lezer).
	_tel_download(rij.name)
	naam = (re.sub(r'[\\/:*?"<>|\r\n]+', "_", rij.naam).strip() or "overdracht") + ".zip"
	return Response(
		_zipstroom([(b.naam, b.s3_sleutel, cint(b.grootte)) for b in bestanden], s3.lezer()),
		mimetype="application/zip",
		headers={
			"Content-Disposition": "attachment; filename=\"{}\"; filename*=UTF-8''{}".format(
				naam.encode("ascii", "replace").decode().replace('"', ""), quote(naam)
			),
			"Cache-Control": "no-store",
		},
		direct_passthrough=True,
	)
