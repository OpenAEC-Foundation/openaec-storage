"""Rechten in OpenAEC Storage: alles volgt uit het lidmaatschap van een ruimte (Ruimtelid).

Rollen: beheerder (alles, ook leden), schrijver (lezen en wijzigen), lezer en gast (lezen).
Administrator en System Manager hebben overal toegang.
"""

import frappe
from frappe import _

LEZEN = frozenset({"beheerder", "schrijver", "lezer", "gast"})
SCHRIJVEN = frozenset({"beheerder", "schrijver"})
LEESACTIES = frozenset({"read", "select", "print", "report", "export", "email", "share"})


def is_superbeheer(gebruiker: str | None = None) -> bool:
	gebruiker = gebruiker or frappe.session.user
	return gebruiker == "Administrator" or "System Manager" in frappe.get_roles(gebruiker)


def rol_in_ruimte(ruimte: str, gebruiker: str | None = None) -> str | None:
	gebruiker = gebruiker or frappe.session.user
	return frappe.db.get_value(
		"Ruimtelid", {"parent": ruimte, "parenttype": "Ruimte", "gebruiker": gebruiker}, "rol"
	)


def mag(ruimte: str, actie: str, gebruiker: str | None = None, ruimte_zelf: bool = False) -> bool:
	"""Mag `gebruiker` deze actie in de ruimte? `ruimte_zelf`: het gaat om de ruimte-instellingen (leden)."""
	gebruiker = gebruiker or frappe.session.user
	if is_superbeheer(gebruiker):
		return True
	rol = rol_in_ruimte(ruimte, gebruiker)
	if not rol:
		return False
	if actie in LEESACTIES:
		return rol in LEZEN
	if ruimte_zelf:
		return rol == "beheerder"
	return rol in SCHRIJVEN


def vereis(ruimte: str, actie: str) -> None:
	if not ruimte or not frappe.db.exists("Ruimte", ruimte):
		frappe.throw(_("Ruimte bestaat niet"), frappe.DoesNotExistError)
	if not mag(ruimte, actie):
		frappe.throw(_("Geen toegang tot deze ruimte"), frappe.PermissionError)


def heeft_recht(doc, ptype=None, user=None, debug=False):
	"""has_permission-hook voor alle opslag-DocTypes."""
	ptype = ptype or "read"
	if doc.doctype == "Ruimte":
		if doc.is_new() or not doc.name or not frappe.db.exists("Ruimte", doc.name):
			return True  # een nieuwe ruimte aanmaken mag; de maker wordt beheerder
		return mag(doc.name, ptype, user, ruimte_zelf=True)
	ruimte = doc.get("ruimte")
	if not ruimte:
		return is_superbeheer(user)
	return mag(ruimte, ptype, user)


def _conditie(tabel: str, kolom: str, user: str | None) -> str:
	user = user or frappe.session.user
	if is_superbeheer(user):
		return ""
	return "`tab{}`.`{}` in (select parent from `tabRuimtelid` where parenttype = 'Ruimte' and gebruiker = {})".format(
		tabel, kolom, frappe.db.escape(user)
	)


def query_ruimte(user=None, doctype=None):
	return _conditie("Ruimte", "name", user)


def query_map(user=None, doctype=None):
	return _conditie("Opslagmap", "ruimte", user)


def query_bestand(user=None, doctype=None):
	return _conditie("Opslagbestand", "ruimte", user)


def query_wijziging(user=None, doctype=None):
	return _conditie("Opslagwijziging", "ruimte", user)
