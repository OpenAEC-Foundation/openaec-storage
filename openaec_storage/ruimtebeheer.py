"""Beheer van ruimtes: archiveren en terugzetten, en de lijst voor de beheeromgeving (ook de gearchiveerde).

Archiveren mag een beheerder van de ruimte of een beheerder van de omgeving (`rechten.mag_ruimte_maken`). Een
gearchiveerde ruimte is voor iedereen alleen te lezen (zie rechten.py), staat niet in `api.ruimtes` (dus niet op
/opslag en niet in de desktop-app, die er dan mee stopt) en zijn deellinks werken niet zolang hij gearchiveerd is.
Beide handelingen komen in de wijzigingenlijst (Opslagwijziging, soort `gearchiveerd` of `teruggezet`).
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from openaec_storage.rechten import mag_ruimte_beheren, mag_ruimte_maken


def _mag_beheren(ruimte: str) -> bool:
	return mag_ruimte_beheren(ruimte) or mag_ruimte_maken()


def _ruimte_voor_beheer(ruimte: str):
	if not ruimte or not frappe.db.exists("Ruimte", ruimte):
		frappe.throw(_("Ruimte bestaat niet"), frappe.DoesNotExistError)
	if not _mag_beheren(ruimte):
		frappe.throw(
			_("Alleen een beheerder van de ruimte of van de omgeving kan dit doen"), frappe.PermissionError
		)
	return frappe.get_doc("Ruimte", ruimte)


def _stand(doc) -> dict:
	return {
		"ruimte": doc.name,
		"gearchiveerd": bool(cint(doc.gearchiveerd)),
		"gearchiveerd_op": doc.gearchiveerd_op,
		"gearchiveerd_door": doc.gearchiveerd_door,
	}


def _zet_archief(doc, gearchiveerd: bool) -> None:
	from openaec_storage.api import _log

	doc.gearchiveerd = 1 if gearchiveerd else 0
	doc.gearchiveerd_op = now_datetime() if gearchiveerd else None
	doc.gearchiveerd_door = frappe.session.user if gearchiveerd else None
	doc.flags.via_archief = True
	doc.save(ignore_permissions=True)
	_log(doc.name, "gearchiveerd" if gearchiveerd else "teruggezet", "Ruimte", doc.name, doc.naam)


@frappe.whitelist(methods=["POST"])
def archiveer(ruimte: str) -> dict:
	"""Zet de ruimte in het archief: alleen nog lezen. Nogmaals aanroepen verandert niets."""
	doc = _ruimte_voor_beheer(ruimte)
	if not cint(doc.gearchiveerd):
		_zet_archief(doc, True)
	return _stand(doc)


@frappe.whitelist(methods=["POST"])
def herstel(ruimte: str) -> dict:
	"""Haal de ruimte uit het archief: weer te wijzigen en zichtbaar op /opslag en in de desktop-app."""
	doc = _ruimte_voor_beheer(ruimte)
	if cint(doc.gearchiveerd):
		_zet_archief(doc, False)
	return _stand(doc)


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
		fields=["name", "naam", "soort", "gearchiveerd", "gearchiveerd_op", "gearchiveerd_door"],
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
	for r in ruimtes:
		r["gearchiveerd"] = bool(cint(r.gearchiveerd))
		r["aantal_leden"] = cint(leden.get(r.name))
	return ruimtes
