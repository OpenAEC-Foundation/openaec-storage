"""Rechten in OpenAEC Storage: alles volgt uit het lidmaatschap van een ruimte (Ruimtelid).

Rollen: beheerder (alles, ook leden), schrijver (lezen en wijzigen), lezer en gast (lezen).
Administrator en System Manager hebben overal toegang.

Een nieuwe ruimte aanmaken mag alleen een beheerder van de omgeving (`mag_ruimte_maken`); wie hem aanmaakt, wordt
er beheerder van (Ruimte.before_insert). Beheerder van een ruimte zijn maakt iemand nog geen beheerder van de omgeving.
"""

import frappe
from frappe import _

LEZEN = frozenset({"beheerder", "schrijver", "lezer", "gast"})
SCHRIJVEN = frozenset({"beheerder", "schrijver"})
LEESACTIES = frozenset({"read", "select", "print", "report", "export", "email", "share"})

# Rollen die een nieuwe ruimte mogen aanmaken; site_config `openaec_storage_ruimte_makers` vervangt de lijst.
# De OpenAEC-rollen zijn de beheerrollen uit openaec_beheer: zonder die app bestaan ze niet en blijven
# Administrator en System Manager over.
RUIMTE_MAKERS = ("System Manager", "OpenAEC Eigenaar", "OpenAEC Beheerder")


def is_superbeheer(gebruiker: str | None = None) -> bool:
	gebruiker = gebruiker or frappe.session.user
	return gebruiker == "Administrator" or "System Manager" in frappe.get_roles(gebruiker)


def ruimte_makers() -> list[str]:
	"""De rollen die een nieuwe ruimte mogen aanmaken (in de site_config een lijst, of tekst met komma's)."""
	rollen = frappe.conf.get("openaec_storage_ruimte_makers")
	if rollen is None:
		return list(RUIMTE_MAKERS)
	if isinstance(rollen, str):
		rollen = rollen.split(",")
	return [rol.strip() for rol in rollen if rol and rol.strip()]


def mag_ruimte_maken(gebruiker: str | None = None) -> bool:
	"""Mag `gebruiker` een nieuwe ruimte aanmaken? Administrator en System Manager altijd, verder de `ruimte_makers`."""
	gebruiker = gebruiker or frappe.session.user
	if gebruiker == "Guest":
		return False
	if is_superbeheer(gebruiker):
		return True
	return not set(ruimte_makers()).isdisjoint(frappe.get_roles(gebruiker))


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
			return mag_ruimte_maken(user)  # alleen een beheerder van de omgeving; de maker wordt ruimtebeheerder
		return mag(doc.name, ptype, user, ruimte_zelf=True)
	ruimte = doc.get("ruimte")
	if not ruimte:
		return is_superbeheer(user)
	if doc.doctype == "Opslagbestand":
		return mag_op_plek(ruimte, doc.get("map"), ptype, doc.name, user)
	if doc.doctype == "Opslagmap":
		return mag_op_plek(ruimte, doc.name if not doc.is_new() else doc.get("bovenliggende"), ptype, None, user)
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


# --- delingen met gebruikers (ontwerp §3): extra rechten op een map en alles eronder ------------------


def voorouders(map: str | None) -> list[str]:
	"""De map zelf en alle bovenliggende mappen (van binnen naar buiten)."""
	keten, huidige, gezien = [], map, set()
	while huidige and huidige not in gezien:
		gezien.add(huidige)
		keten.append(huidige)
		huidige = frappe.db.get_value("Opslagmap", huidige, "bovenliggende")
	return keten


def deling_recht(gebruiker: str, map: str | None, bestand: str | None = None) -> str | None:
	"""Het hoogste recht ('schrijven' of 'lezen') dat een gebruiker via delingen heeft op deze plek."""
	doelen = [("Opslagmap", m) for m in voorouders(map)]
	if bestand:
		doelen.append(("Opslagbestand", bestand))
	if not doelen:
		return None
	beste = None
	for doctype, doel in doelen:
		for rij in frappe.get_all(
			"Opslagdeling",
			filters={"soort": "gebruiker", "gebruiker": gebruiker, "ingetrokken": 0, "doel_doctype": doctype, "doel": doel},
			fields=["rechten", "verloopt"],
			ignore_permissions=True,
		):
			if rij.verloopt and rij.verloopt < frappe.utils.now_datetime():
				continue
			if rij.rechten == "schrijven":
				return "schrijven"
			beste = "lezen"
	return beste


def mag_op_plek(ruimte: str, map: str | None, actie: str, bestand: str | None = None, gebruiker: str | None = None) -> bool:
	"""Rechten op een plek in de ruimte: via het lidmaatschap, of via een deling op de plek of erboven."""
	gebruiker = gebruiker or frappe.session.user
	if mag(ruimte, actie, gebruiker):
		return True
	recht = deling_recht(gebruiker, map, bestand)
	if not recht:
		return False
	return actie in LEESACTIES or recht == "schrijven"


def vereis_op_plek(ruimte: str, map: str | None, actie: str, bestand: str | None = None) -> None:
	if not ruimte or not frappe.db.exists("Ruimte", ruimte):
		frappe.throw(_("Ruimte bestaat niet"), frappe.DoesNotExistError)
	if not mag_op_plek(ruimte, map, actie, bestand):
		frappe.throw(_("Geen toegang tot deze plek"), frappe.PermissionError)


# --- overdrachten: losse bestanden versturen, los van de ruimtes --------------------------------------


def mag_overdragen(gebruiker: str | None = None) -> bool:
	"""Mag `gebruiker` een overdracht maken? Wie mag delen: schrijver of beheerder in een ruimte, of een beheerder van
	de omgeving. Site_config `openaec_storage_overdracht_rollen` (lijst) geeft het recht ook aan rollen. Gasten nooit."""
	gebruiker = gebruiker or frappe.session.user
	if not gebruiker or gebruiker == "Guest":
		return False
	if mag_ruimte_maken(gebruiker):
		return True
	rollen = frappe.conf.get("openaec_storage_overdracht_rollen") or []
	if isinstance(rollen, str):
		rollen = rollen.split(",")
	if not {r.strip() for r in rollen if r and r.strip()}.isdisjoint(frappe.get_roles(gebruiker)):
		return True
	return bool(
		frappe.db.exists(
			"Ruimtelid", {"parenttype": "Ruimte", "gebruiker": gebruiker, "rol": ("in", sorted(SCHRIJVEN))}
		)
	)


def recht_op_overdracht(doc, ptype=None, user=None, debug=False):
	"""has_permission-hook voor Overdracht en Overdrachtbestand: alleen de maker (en het beheer)."""
	user = user or frappe.session.user
	if is_superbeheer(user):
		return True
	eigenaar = doc.owner if doc.doctype == "Overdracht" else frappe.db.get_value("Overdracht", doc.get("overdracht"), "owner")
	return bool(eigenaar) and eigenaar == user and not doc.is_new()


def query_overdracht(user=None, doctype=None):
	user = user or frappe.session.user
	if is_superbeheer(user):
		return ""
	return "`tabOverdracht`.`owner` = {}".format(frappe.db.escape(user))
