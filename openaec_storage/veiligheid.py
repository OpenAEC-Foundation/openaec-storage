"""Veiligheidsgrenzen voor bulkacties: verwijderen, verplaatsen (ook hernoemen) en bewerken (nieuwe versies).

Een beheerder stelt per actie twee grenzen in: hoeveel bestanden iemand **in één keer** mag raken en hoeveel **per
tijdvak** (standaard een uur). Wat erboven gaat, weigert de server met een vaste foutcode en een uitleg; de poging komt
in het logboek (`Opslagbulkactie`) en de beheerders krijgen een melding. De grenzen gelden langs elke weg (browser,
desktop-app, WebDAV, API), want ze worden hier in de API gecontroleerd, niet in een frontend.

- **Per keer** gaat over één aanroep: `verwijder_bulk`/`verplaats_bulk` met hun aantal, een map met alles wat erin
  staat (`verwijder_map`, `verplaats_map`) en `upload_klein` met de bestanden die al bestonden.
- **Per tijdvak** telt alles van één gebruiker in een vast venster van `venster_minuten` (een teller in de cache);
  zo houdt ook een client die per bestand een aanroep doet (sync, WebDAV, een script) op bij de grens.
- **Per rol of groep**: een regel geldt voor wie die rol heeft of lid is van die groep (de groepen komen via de hook
  `openaec_storage_groepen_van(gebruiker) -> list[str]`, bijvoorbeeld uit het beheer). Past er meer dan één regel,
  dan telt per actie de ruimste grens (0 = geen grens). Past er geen enkele regel, dan geldt de standaard.
- **Blokkeren**: een beheerder kan een account tijdelijk blokkeren; alle acties hierboven (en uploaden) weigeren dan
  met `account_geblokkeerd`.
- **Terugdraaien**: een uitgevoerde bulkactie kan in één keer terug (verwijderde items uit de prullenbak, verplaatste
  items naar hun oude plek), zie `draai_terug`.

De instellingen en blokkades staan in de standaardwaarden van de site (`frappe.db.set_default`), het logboek in het
DocType `Opslagbulkactie`. De rechtencontrole voor het beheren hoort bij de aanroeper (het beheer); deze functies
zijn daarom niet whitelisted.
"""

import json
import os

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from openaec_storage.rechten import weiger

ACTIES = ("verwijderen", "verplaatsen", "bewerken")
SLEUTEL_INSTELLINGEN = "openaec_storage_veiligheid"
SLEUTEL_BLOKKADES = "openaec_storage_blokkades"
TELLER = "openaec_storage:veiligheid:"
MELDING_GEDAAN = "openaec_storage:veiligheid:gemeld:"
STANDAARD_VENSTER = 60
MAX_ITEMS_IN_LOG = 2000

STANDAARD = {
	"aan": 0,
	"venster_minuten": STANDAARD_VENSTER,
	"melden": 1,
	"standaard": {actie: {"per_keer": 0, "per_tijdvak": 0} for actie in ACTIES},
	"regels": [],
}

WOORD = {
	"verwijderen": _("verwijderen"),
	"verplaatsen": _("verplaatsen of hernoemen"),
	"bewerken": _("bewerken"),
}


# --- instellingen ------------------------------------------------------------------------------------


def _grens(waarde) -> dict:
	waarde = waarde if isinstance(waarde, dict) else {}
	return {"per_keer": max(0, cint(waarde.get("per_keer"))), "per_tijdvak": max(0, cint(waarde.get("per_tijdvak")))}


def _grenzen(waarde) -> dict:
	waarde = waarde if isinstance(waarde, dict) else {}
	return {actie: _grens(waarde.get(actie)) for actie in ACTIES}


def schoon(waarden) -> dict:
	"""Controleer en normaliseer instellingen (getallen >= 0, alleen bekende acties, regels met soort rol of groep)."""
	if isinstance(waarden, str):
		waarden = json.loads(waarden or "{}")
	waarden = waarden if isinstance(waarden, dict) else {}
	regels = []
	for regel in waarden.get("regels") or []:
		if not isinstance(regel, dict):
			continue
		soort, naam = regel.get("soort"), (regel.get("naam") or "").strip()
		if soort not in ("rol", "groep") or not naam:
			frappe.throw(_("Een regel heeft een rol of een groep nodig"))
		regels.append({"soort": soort, "naam": naam, **_grenzen(regel.get("grenzen") or regel)})
	venster = cint(waarden.get("venster_minuten")) or STANDAARD_VENSTER
	if not 1 <= venster <= 24 * 60:
		frappe.throw(_("Het tijdvak ligt tussen 1 minuut en 24 uur"))
	return {
		"aan": 1 if cint(waarden.get("aan")) else 0,
		"venster_minuten": venster,
		"melden": 1 if cint(waarden.get("melden", 1)) else 0,
		"standaard": _grenzen(waarden.get("standaard")),
		"regels": [{"soort": r["soort"], "naam": r["naam"], "grenzen": {a: r[a] for a in ACTIES}} for r in regels],
	}


def instellingen() -> dict:
	"""De instellingen van de site (met de standaardwaarden voor wat ontbreekt)."""
	ruw = frappe.db.get_default(SLEUTEL_INSTELLINGEN)
	try:
		return schoon(json.loads(ruw)) if ruw else schoon(STANDAARD)
	except Exception:
		frappe.log_error(title="openaec_storage: veiligheidsinstellingen onleesbaar")
		return schoon(STANDAARD)


def zet_instellingen(waarden) -> dict:
	"""Bewaar nieuwe instellingen (de aanroeper controleert de rechten)."""
	nieuw = schoon(waarden)
	frappe.db.set_default(SLEUTEL_INSTELLINGEN, json.dumps(nieuw))
	return nieuw


# --- wie, welke grens --------------------------------------------------------------------------------


def groepen_van(gebruiker: str) -> set[str]:
	uit: set[str] = set()
	for pad in frappe.get_hooks("openaec_storage_groepen_van"):
		try:
			uit |= set(frappe.get_attr(pad)(gebruiker) or [])
		except Exception:
			frappe.log_error(title="openaec_storage: groepen voor de veiligheidsgrenzen niet opgehaald")
	return uit


def grenzen_voor(gebruiker: str | None = None, waarden: dict | None = None) -> dict:
	"""De grenzen die voor `gebruiker` gelden: per actie `{per_keer, per_tijdvak}` (0 = geen grens)."""
	gebruiker = gebruiker or frappe.session.user
	waarden = waarden or instellingen()
	regels = waarden["regels"]
	if regels:
		rollen = set(frappe.get_roles(gebruiker))
		groepen = groepen_van(gebruiker) if any(r["soort"] == "groep" for r in regels) else set()
		passend = [r for r in regels if (r["naam"] in rollen if r["soort"] == "rol" else r["naam"] in groepen)]
	else:
		passend = []
	if not passend:
		return waarden["standaard"]
	uit = {}
	for actie in ACTIES:
		uit[actie] = {}
		for soort in ("per_keer", "per_tijdvak"):
			waarden_ = [r["grenzen"][actie][soort] for r in passend]
			# De ruimste grens telt; 0 (geen grens) is het ruimst.
			uit[actie][soort] = 0 if 0 in waarden_ else max(waarden_)
	return uit


# --- blokkades ---------------------------------------------------------------------------------------


def blokkades() -> dict[str, str]:
	"""Actieve blokkades: `{gebruiker: tot}` (verlopen blokkades vallen weg)."""
	try:
		ruw = json.loads(frappe.db.get_default(SLEUTEL_BLOKKADES) or "{}")
	except Exception:
		ruw = {}
	nu = now_datetime()
	return {g: tot for g, tot in (ruw or {}).items() if tot and get_datetime(tot) > nu}


def blokkeer(gebruiker: str, uren: int = 24) -> str:
	"""Blokkeer de bulkacties en het schrijven in de opslag van `gebruiker` voor `uren` uur; geeft het einde."""
	if not frappe.db.exists("User", gebruiker) or gebruiker in ("Administrator", "Guest"):
		frappe.throw(_("Onbekende gebruiker"))
	uren = cint(uren)
	if not 1 <= uren <= 24 * 30:
		frappe.throw(_("Blokkeer tussen 1 uur en 30 dagen"))
	tot = str(add_to_date(now_datetime(), hours=uren))
	nu = blokkades()
	nu[gebruiker] = tot
	frappe.db.set_default(SLEUTEL_BLOKKADES, json.dumps(nu))
	return tot


def deblokkeer(gebruiker: str) -> None:
	nu = blokkades()
	nu.pop(gebruiker, None)
	frappe.db.set_default(SLEUTEL_BLOKKADES, json.dumps(nu))


def is_geblokkeerd(gebruiker: str | None = None) -> str | None:
	return blokkades().get(gebruiker or frappe.session.user)


# --- controleren -------------------------------------------------------------------------------------


def _teller_sleutel(gebruiker: str, actie: str, venster: int) -> str:
	vak = int(now_datetime().timestamp() // (venster * 60))
	return frappe.cache.make_key(f"{TELLER}{gebruiker}:{actie}:{venster}:{vak}")


def in_tijdvak(gebruiker: str, actie: str, venster: int | None = None) -> int:
	venster = venster or instellingen()["venster_minuten"]
	return cint(frappe.cache.get(_teller_sleutel(gebruiker, actie, venster)) or 0)


def _tel(gebruiker: str, actie: str, venster: int, aantal: int) -> None:
	sleutel = _teller_sleutel(gebruiker, actie, venster)
	frappe.cache.incrby(sleutel, aantal)
	frappe.cache.expire(sleutel, venster * 60 + 60)


def controleer(actie: str, aantal: int, bron: str | None = None, ruimte: str | None = None, items: list | None = None) -> None:
	"""Mag de aanroeper nu `aantal` bestanden `actie`? Zo niet: logboek, melding aan de beheerders en een weigering
	met foutcode (`bulk_grens`, `bulk_grens_tijdvak` of `account_geblokkeerd`). Mag het wel, dan telt het mee voor
	het tijdvak. Binnen een bulkaanroep die al als geheel gecontroleerd is (`frappe.flags.oa_bulk_gecontroleerd`)
	doet een aanroep per item niets."""
	if frappe.flags.oa_bulk_gecontroleerd or actie not in ACTIES:
		return
	gebruiker = frappe.session.user
	tot = is_geblokkeerd(gebruiker)
	if tot:
		_weiger(
			actie,
			aantal,
			"geblokkeerd",
			0,
			bron,
			ruimte,
			items,
			_("Je account is tot {0} geblokkeerd voor het wijzigen van bestanden. Neem contact op met je beheerder.").format(
				frappe.utils.format_datetime(tot, "dd-MM-yyyy HH:mm")
			),
			"account_geblokkeerd",
		)
	waarden = instellingen()
	if not waarden["aan"] or aantal <= 0:
		return
	grens = grenzen_voor(gebruiker, waarden)[actie]
	if grens["per_keer"] and aantal > grens["per_keer"]:
		_weiger(
			actie,
			aantal,
			"per_keer",
			grens["per_keer"],
			bron,
			ruimte,
			items,
			_(
				"Je mag hoogstens {0} bestanden in één keer {1}; dit waren er {2}. Doe het in kleinere delen of vraag je beheerder om hulp."
			).format(grens["per_keer"], WOORD[actie], aantal),
			"bulk_grens",
		)
	venster = waarden["venster_minuten"]
	if grens["per_tijdvak"]:
		al = in_tijdvak(gebruiker, actie, venster)
		if al + aantal > grens["per_tijdvak"]:
			_weiger(
				actie,
				aantal,
				"per_tijdvak",
				grens["per_tijdvak"],
				bron,
				ruimte,
				items,
				_(
					"Je mag hoogstens {0} bestanden per {1} minuten {2}; die grens is bereikt. Probeer het later opnieuw of vraag je beheerder om hulp."
				).format(grens["per_tijdvak"], venster, WOORD[actie]),
				"bulk_grens_tijdvak",
			)
	_tel(gebruiker, actie, venster, aantal)


def _weiger(actie, aantal, reden, grens, bron, ruimte, items, melding, foutcode) -> None:
	"""Leg de weigering vast en meld hem, en weiger. Een poging boven de grens per keer komt altijd in het logboek;
	bij de grens per tijdvak of een blokkade alleen de eerste per tijdvak (een client die per bestand blijft
	proberen, maakt anders een stroom regels)."""
	gebruiker = frappe.session.user
	try:
		eerste = _eerste_keer(gebruiker, actie, reden)
		if reden == "per_keer" or eerste:
			log(actie, aantal, "geweigerd", bron=bron, ruimte=ruimte, items=items, reden=reden, grens=grens)
		if eerste:
			_meld_beheerders(gebruiker, actie, aantal, reden, grens)
		# De weigering rolt het verzoek terug: het logboek en de melding moeten blijven staan.
		if not frappe.flags.in_test:
			frappe.db.commit()
	except Exception:
		frappe.log_error(title="openaec_storage: weigering van een bulkactie niet gelogd")
	weiger(melding, foutcode, frappe.PermissionError)


# --- logboek -----------------------------------------------------------------------------------------


def log(
	actie: str,
	aantal: int,
	uitkomst: str,
	bron: str | None = None,
	ruimte: str | None = None,
	items: list | None = None,
	reden: str | None = None,
	grens: int | None = None,
) -> str:
	doc = frappe.get_doc(
		{
			"doctype": "Opslagbulkactie",
			"gebruiker": frappe.session.user,
			"actie": actie,
			"aantal": cint(aantal),
			"uitkomst": uitkomst,
			"reden": reden,
			"grens": grens,
			"bron": bron,
			"ruimte": ruimte,
			"items": json.dumps((items or [])[:MAX_ITEMS_IN_LOG]) if items else None,
		}
	).insert(ignore_permissions=True)
	return doc.name


def logboek(pagina: int = 1, per_pagina: int = 25, gebruiker: str | None = None, uitkomst: str | None = None) -> dict:
	"""Het logboek van bulkacties, nieuwste eerst (zonder de items zelf, wel of terugdraaien kan)."""
	pagina, per_pagina = max(1, cint(pagina)), min(200, max(1, cint(per_pagina) or 25))
	filters = {}
	if gebruiker:
		filters["gebruiker"] = ("like", f"%{gebruiker}%")
	if uitkomst:
		filters["uitkomst"] = uitkomst
	velden = ["name", "creation", "gebruiker", "actie", "aantal", "uitkomst", "reden", "grens", "bron", "ruimte",
		"teruggedraaid_door", "teruggedraaid_op", "items"]
	rijen = frappe.get_all(
		"Opslagbulkactie",
		filters=filters,
		fields=velden,
		order_by="creation desc",
		start=(pagina - 1) * per_pagina,
		limit=per_pagina,
	)
	totaal = frappe.db.count("Opslagbulkactie", filters)
	ruimtes = {r.ruimte for r in rijen if r.ruimte}
	namen = dict(frappe.get_all("Ruimte", filters={"name": ("in", list(ruimtes))}, fields=["name", "naam"], as_list=True)) if ruimtes else {}
	uit = []
	for r in rijen:
		items = r.pop("items")
		r["ruimte_naam"] = namen.get(r.ruimte)
		r["terug_te_draaien"] = bool(items) and r.uitkomst == "uitgevoerd" and r.actie in ("verwijderen", "verplaatsen")
		uit.append(r)
	return {"items": uit, "totaal": totaal, "pagina": pagina, "per_pagina": per_pagina}


# --- melden ------------------------------------------------------------------------------------------


def beheerders() -> list[str]:
	"""Wie een melding krijgt: via de hook `openaec_storage_beheerders`, anders de System Managers."""
	for pad in frappe.get_hooks("openaec_storage_beheerders"):
		try:
			return list(frappe.get_attr(pad)() or [])
		except Exception:
			frappe.log_error(title="openaec_storage: beheerders voor een melding niet opgehaald")
	return frappe.get_all(
		"Has Role",
		filters={"role": "System Manager", "parenttype": "User", "parent": ("not in", ["Administrator", "Guest"])},
		pluck="parent",
		distinct=True,
	)


def _eerste_keer(gebruiker: str, actie: str, reden: str) -> bool:
	"""De eerste weigering van deze soort voor deze gebruiker en actie in het tijdvak?"""
	sleutel = frappe.cache.make_key(f"{MELDING_GEDAAN}{gebruiker}:{actie}:{reden}")
	if frappe.cache.get(sleutel):
		return False
	frappe.cache.set(sleutel, 1, ex=instellingen()["venster_minuten"] * 60)
	return True


def _meld_beheerders(gebruiker: str, actie: str, aantal: int, reden: str, grens: int) -> None:
	"""Een melding (Notification Log) aan de beheerders; één per gebruiker, actie en tijdvak (zie `_eerste_keer`)."""
	waarden = instellingen()
	if not waarden["melden"]:
		return
	if reden == "geblokkeerd":
		tekst = _("{0} probeerde {1} bestanden te {2}, maar het account is geblokkeerd.").format(gebruiker, aantal, WOORD[actie])
	elif reden == "per_keer":
		tekst = _("{0} probeerde {1} bestanden in één keer te {2}; de grens is {3}. De server heeft het geweigerd.").format(
			gebruiker, aantal, WOORD[actie], grens
		)
	else:
		tekst = _("{0} heeft de grens van {1} bestanden {2} per {3} minuten bereikt; de server weigert de rest.").format(
			gebruiker, grens, WOORD[actie], waarden["venster_minuten"]
		)
	onderwerp = _("Veiligheidsgrens in Docs: {0}").format(gebruiker)
	for ontvanger in beheerders():
		if ontvanger == gebruiker:
			continue
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": ontvanger,
				"type": "Alert",
				"subject": onderwerp,
				"email_content": tekst,
				"document_type": "Opslagbulkactie",
			}
		).insert(ignore_permissions=True)


# --- terugdraaien ------------------------------------------------------------------------------------


def draai_terug(bulkactie: str) -> dict:
	"""Draai een uitgevoerde bulkactie in één keer terug (de aanroeper controleert de rechten). Verwijderd: terug uit de
	prullenbak (een map met alles wat er tegelijk mee weg ging; een naam die intussen bezet is, krijgt "(hersteld)").
	Verplaatst: terug naar de oude map met de oude naam (een bezette plek wordt overgeslagen en gemeld)."""
	from openaec_storage import api

	doc = frappe.get_doc("Opslagbulkactie", bulkactie)
	if doc.uitkomst != "uitgevoerd" or doc.actie not in ("verwijderen", "verplaatsen") or not doc.items:
		frappe.throw(_("Deze bulkactie kan niet worden teruggedraaid"))
	items = json.loads(doc.items)
	gelukt, mislukt = 0, []
	for item in items:
		frappe.db.savepoint("terugdraaien")
		try:
			if doc.actie == "verwijderen":
				_herstel_item(api, item)
			else:
				_terug_verplaatsen(api, item)
			gelukt += 1
		except Exception as fout:
			frappe.db.rollback(save_point="terugdraaien")
			frappe.clear_messages()
			mislukt.append({"naam": item.get("naam"), "fout": str(fout)})
	doc.db_set({"uitkomst": "teruggedraaid", "teruggedraaid_door": frappe.session.user, "teruggedraaid_op": now_datetime()})
	return {"gelukt": gelukt, "mislukt": mislukt}


def _herstel_item(api, item: dict) -> None:
	if item["soort"] == "bestand":
		b = frappe.db.get_value("Opslagbestand", item["id"], ["name", "ruimte", "map", "naam", "verwijderd_op"], as_dict=True)
		if not b or not b.verwijderd_op:
			return
		naam = b.naam
		if api._actief_bestand(b.ruimte, b.map, naam):
			stam, ext = os.path.splitext(naam)
			naam = f"{stam} (hersteld){ext}"
		frappe.db.set_value("Opslagbestand", b.name, {"verwijderd_op": None, "naam": naam})
		api._log(b.ruimte, "hersteld", "Opslagbestand", b.name, naam)
		return
	m = frappe.db.get_value("Opslagmap", item["id"], ["name", "ruimte", "bovenliggende", "naam", "verwijderd_op"], as_dict=True)
	if not m or not m.verwijderd_op:
		return
	moment = m.verwijderd_op
	naam = m.naam
	if api._actieve_map(m.ruimte, m.bovenliggende, naam) or api._actief_bestand(m.ruimte, m.bovenliggende, naam):
		naam = f"{naam} (hersteld)"
	# Alles wat op hetzelfde moment met de map weg ging, komt terug (zie verwijder_map).
	mappen = [x for x in api._submappen(m.name, ook_verwijderd=True)]
	for x in mappen:
		if frappe.db.get_value("Opslagmap", x, "verwijderd_op") == moment:
			frappe.db.set_value("Opslagmap", x, "verwijderd_op", None)
	frappe.db.set_value("Opslagmap", m.name, "naam", naam)
	for b in frappe.get_all("Opslagbestand", filters={"map": ("in", mappen), "verwijderd_op": moment}, pluck="name"):
		frappe.db.set_value("Opslagbestand", b, "verwijderd_op", None)
	api._log(m.ruimte, "hersteld", "Opslagmap", m.name, naam)


def _terug_verplaatsen(api, item: dict) -> None:
	oude_map, oude_naam = item.get("oude_map") or None, item.get("oude_naam") or item.get("naam")
	if item["soort"] == "bestand":
		b = frappe.db.get_value("Opslagbestand", item["id"], ["name", "ruimte", "map", "naam", "verwijderd_op"], as_dict=True)
		if not b or b.verwijderd_op:
			frappe.throw(_("Het bestand bestaat niet meer"))
		if (b.map or None) == oude_map and b.naam == oude_naam:
			return
		if api._actief_bestand(b.ruimte, oude_map, oude_naam) or api._actieve_map(b.ruimte, oude_map, oude_naam):
			frappe.throw(_("Op de oude plek staat al iets met deze naam"))
		frappe.db.set_value("Opslagbestand", b.name, {"map": oude_map, "naam": oude_naam})
		api._log(b.ruimte, "verplaatst", "Opslagbestand", b.name, oude_naam)
		return
	m = frappe.db.get_value("Opslagmap", item["id"], ["name", "ruimte", "bovenliggende", "naam", "verwijderd_op"], as_dict=True)
	if not m or m.verwijderd_op:
		frappe.throw(_("De map bestaat niet meer"))
	if (m.bovenliggende or None) == oude_map and m.naam == oude_naam:
		return
	if oude_map and oude_map in api._submappen(m.name):
		frappe.throw(_("Een map kan niet in zichzelf worden geplaatst"))
	if api._actieve_map(m.ruimte, oude_map, oude_naam) or api._actief_bestand(m.ruimte, oude_map, oude_naam):
		frappe.throw(_("Op de oude plek staat al iets met deze naam"))
	frappe.db.set_value("Opslagmap", m.name, {"bovenliggende": oude_map, "naam": oude_naam})
	api._log(m.ruimte, "verplaatst", "Opslagmap", m.name, oude_naam)
