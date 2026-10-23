"""Beheer van ruimtes: archiveren en terugzetten, het eigen quotum, de lijst voor de beheeromgeving (ook de
gearchiveerde, met gebruik en quotum per ruimte) en het activiteitenlog per ruimte (uit Opslagwijziging).

Archiveren mag een beheerder van de ruimte of een beheerder van de omgeving (`rechten.mag_ruimte_maken`). Een
gearchiveerde ruimte is voor iedereen alleen te lezen (zie rechten.py), staat niet in `api.ruimtes` (dus niet op
/opslag en niet in de desktop-app, die er dan mee stopt) en zijn deellinks werken niet zolang hij gearchiveerd is.
Beide handelingen komen in de wijzigingenlijst (Opslagwijziging, soort `gearchiveerd` of `teruggezet`).

`namens` (archiveer, herstel, zet_quotum en mapsjablonen.pas_toe): een serviceaanroep uit het beheer
(Administrator of System Manager) geeft mee voor wie hij handelt. Dat komt naast de echte aanroeper in de
wijzigingenlijst; de rechten blijven die van de aanroeper. Van een andere aanroeper wordt `namens` genegeerd.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, get_fullname, now_datetime

from openaec_storage.rechten import mag_ruimte_beheren, mag_ruimte_maken
from openaec_storage.rechten import namens as bepaal_namens


def _mag_beheren(ruimte: str) -> bool:
	return mag_ruimte_beheren(ruimte) or mag_ruimte_maken()


def ruimte_voor_beheer(ruimte: str):
	if not ruimte or not frappe.db.exists("Ruimte", ruimte):
		frappe.throw(_("Ruimte bestaat niet"), frappe.DoesNotExistError)
	if not _mag_beheren(ruimte):
		frappe.throw(
			_("Alleen een beheerder van de ruimte of van de omgeving kan dit doen"), frappe.PermissionError
		)
	return frappe.get_doc("Ruimte", ruimte)


def actieve_deellinks(ruimte: str | None = None) -> dict[str, int]:
	"""Aantal geldige deellinks per ruimte (ook uploadlinks), met dezelfde regel als delen._deling: een link die niet
	is ingetrokken en niet is verlopen. Zolang de ruimte gearchiveerd is, werken ze niet; het beheer kan daarvoor
	waarschuwen."""
	voorwaarde, waarden = "", {"nu": now_datetime()}
	if ruimte:
		voorwaarde, waarden["ruimte"] = "and ruimte = %(ruimte)s", ruimte
	return {
		r.ruimte: cint(r.aantal)
		for r in frappe.db.sql(
			f"""select ruimte, count(*) as aantal from `tabOpslagdeling`
			where soort = 'link' and ifnull(ingetrokken, 0) = 0 and (verloopt is null or verloopt >= %(nu)s) {voorwaarde}
			group by ruimte""",
			waarden,
			as_dict=True,
		)
	}


def _stand(doc) -> dict:
	return {
		"ruimte": doc.name,
		"gearchiveerd": bool(cint(doc.gearchiveerd)),
		"gearchiveerd_op": doc.gearchiveerd_op,
		"gearchiveerd_door": doc.gearchiveerd_door,
		"aantal_actieve_deellinks": actieve_deellinks(doc.name).get(doc.name, 0),
	}


def _zet_archief(doc, gearchiveerd: bool, namens: str | None) -> None:
	from openaec_storage.api import _log

	doc.gearchiveerd = 1 if gearchiveerd else 0
	doc.gearchiveerd_op = now_datetime() if gearchiveerd else None
	# Wie besloot: bij een serviceaanroep uit het beheer de gebruiker namens wie; de aanroeper staat in het log.
	doc.gearchiveerd_door = (namens or frappe.session.user) if gearchiveerd else None
	doc.flags.via_archief = True
	doc.save(ignore_permissions=True)
	_log(doc.name, "gearchiveerd" if gearchiveerd else "teruggezet", "Ruimte", doc.name, doc.naam, namens)


@frappe.whitelist(methods=["POST"])
def archiveer(ruimte: str, namens: str | None = None) -> dict:
	"""Zet de ruimte in het archief: alleen nog lezen. Nogmaals aanroepen verandert niets."""
	doc = ruimte_voor_beheer(ruimte)
	if not cint(doc.gearchiveerd):
		_zet_archief(doc, True, bepaal_namens(namens))
	return _stand(doc)


@frappe.whitelist(methods=["POST"])
def herstel(ruimte: str, namens: str | None = None) -> dict:
	"""Haal de ruimte uit het archief: weer te wijzigen en zichtbaar op /opslag en in de desktop-app."""
	doc = ruimte_voor_beheer(ruimte)
	if cint(doc.gearchiveerd):
		_zet_archief(doc, False, bepaal_namens(namens))
	return _stand(doc)


@frappe.whitelist(methods=["POST"])
def zet_quotum(ruimte: str, quotum_gb: float | str | None = None, namens: str | None = None) -> dict:
	"""Eigen quotum van de ruimte in GB (0 of leeg = geen eigen limiet). Alleen een beheerder van de omgeving: het
	quotum is een afspraak met de omgeving, niet iets wat de ruimte zelf verhoogt. De quota van alle ruimtes samen
	mogen groter zijn dan dat van de omgeving (overboeking)."""
	if not ruimte or not frappe.db.exists("Ruimte", ruimte):
		frappe.throw(_("Ruimte bestaat niet"), frappe.DoesNotExistError)
	if not mag_ruimte_maken():
		frappe.throw(_("Alleen een beheerder van de omgeving kan het quotum van een ruimte wijzigen"), frappe.PermissionError)
	from openaec_storage.api import _log

	doc = frappe.get_doc("Ruimte", ruimte)
	vorig = flt(doc.quotum_gb)
	doc.quotum_gb = flt(quotum_gb)
	doc.save(ignore_permissions=True)
	if flt(doc.quotum_gb) != vorig:
		_log(doc.name, "quotum_gewijzigd", "Ruimte", doc.name, "{:g} GB".format(flt(doc.quotum_gb)), bepaal_namens(namens))
	limiet = int(flt(doc.quotum_gb) * 1_000_000_000)
	return {"ruimte": doc.name, "quotum_gb": flt(doc.quotum_gb), "limiet_bytes": limiet or None}


@frappe.whitelist()
def lijst(gearchiveerd: str | int | None = None) -> list[dict]:
	"""Ruimtes voor het beheer, ook de gearchiveerde. Een beheerder van de omgeving ziet ze allemaal, een ander de
	ruimtes waarvan hij beheerder is. `gearchiveerd`: leeg = alle, 0 = alleen actieve, 1 = alleen gearchiveerde."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Log eerst in"), frappe.PermissionError)
	filters = {}
	if gearchiveerd not in (None, ""):
		filters["gearchiveerd"] = 1 if cint(gearchiveerd) else 0
	if not mag_ruimte_maken():
		filters["name"] = (
			"in",
			frappe.get_all(
				"Ruimtelid",
				filters={"parenttype": "Ruimte", "gebruiker": frappe.session.user, "rol": "beheerder"},
				pluck="parent",
				ignore_permissions=True,
			)
			or [""],
		)
	ruimtes = frappe.get_all(
		"Ruimte",
		filters=filters,
		fields=[
			"name", "naam", "soort", "gearchiveerd", "gearchiveerd_op", "gearchiveerd_door", "quotum_gb", "mapsjabloon"
		],
		order_by="naam asc",
		ignore_permissions=True,
	)
	leden = {
		r.parent: r.aantal
		for r in frappe.db.sql(
			"""select parent, count(*) as aantal from `tabRuimtelid` where parenttype = 'Ruimte' group by parent""",
			as_dict=True,
		)
	}
	gebruikt = {
		r.ruimte: r.bytes
		for r in frappe.db.sql(
			"""select ruimte, coalesce(sum(grootte), 0) as bytes from `tabBestandsversie` group by ruimte""", as_dict=True
		)
	}
	links = actieve_deellinks()
	for r in ruimtes:
		r["gearchiveerd"] = bool(cint(r.gearchiveerd))
		r["aantal_actieve_deellinks"] = links.get(r.name, 0)
		r["aantal_leden"] = cint(leden.get(r.name))
		r["gebruikt_bytes"] = cint(gebruikt.get(r.name))
		r["limiet_bytes"] = int(flt(r.quotum_gb) * 1_000_000_000) or None
	return ruimtes


@frappe.whitelist()
def activiteit(ruimte: str, vanaf: str | None = None, start: int = 0, aantal: int = 50) -> dict:
	"""Wat er in de ruimte gebeurde, nieuwste eerst, voor het beheer: een beheerder van de ruimte of van de omgeving.
	`door` is wie de handeling echt aanriep, `namens` voor wie het beheer hem uitvoerde (of leeg).
	`vanaf`: alleen vanaf dit tijdstip (datum of datum-tijd); `start` en `aantal` (hoogstens 500) voor de pagina's."""
	ruimte_voor_beheer(ruimte)
	start, aantal = max(cint(start), 0), min(max(cint(aantal), 1), 500)
	filters = {"ruimte": ruimte}
	if vanaf:
		filters["creation"] = (">=", get_datetime(vanaf))
	totaal = frappe.db.count("Opslagwijziging", filters)
	regels = frappe.get_all(
		"Opslagwijziging",
		filters=filters,
		fields=["name", "creation", "owner", "namens", "soort", "doel_doctype", "doel", "naam"],
		order_by="name desc",
		limit_start=start,
		limit=aantal,
		ignore_permissions=True,
	)
	namen = {}
	for gebruiker in {r.owner for r in regels} | {r.namens for r in regels if r.namens}:
		namen[gebruiker] = get_fullname(gebruiker) if gebruiker not in ("Guest", "Administrator") else gebruiker
	return {
		"ruimte": ruimte,
		"totaal": totaal,
		"start": start,
		"aantal": len(regels),
		"meer": start + len(regels) < totaal,
		"regels": [
			{
				"volgnummer": cint(r.name),
				"tijd": r.creation,
				"soort": r.soort,
				"doel_doctype": r.doel_doctype,
				"doel": r.doel,
				"naam": r.naam,
				"door": r.owner,
				"door_naam": namen[r.owner],
				"namens": r.namens,
				"namens_naam": namen.get(r.namens) if r.namens else None,
			}
			for r in regels
		],
	}
