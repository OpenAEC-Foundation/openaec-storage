"""Rechten in OpenAEC Storage: alles volgt uit het lidmaatschap van een ruimte (Ruimtelid).

Rollen: beheerder (alles, ook leden), schrijver (lezen en wijzigen), lezer en gast (lezen).
Administrator en System Manager hebben overal toegang.

Een nieuwe ruimte aanmaken mag alleen een beheerder van de omgeving (`mag_ruimte_maken`); wie hem aanmaakt, wordt
er beheerder van (Ruimte.before_insert). Beheerder van een ruimte zijn maakt iemand nog geen beheerder van de omgeving.

Daarnaast kan een map of bestand gedeeld zijn met een gebruiker (Opslagdeling, soort `gebruiker`): die heeft dan
lees- of schrijfrecht op de map en alles eronder, ook zonder lid te zijn (`mag_op_plek`). Wie geen lid is, komt
nooit buiten wat gedeeld is (`buiten_deling`), en na intrekken of verlopen meteen nergens meer.

Een gearchiveerde ruimte is voor iedereen alleen te lezen, ook voor het beheer: `mag` en `mag_op_plek` geven geen
schrijfrecht en `vereis`/`vereis_op_plek` weigeren met de foutcode `ruimte_gearchiveerd`. Alleen de instellingen van
de ruimte (leden) blijven te wijzigen door een beheerder.
"""

import frappe
from frappe import _
from frappe.utils import cint

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


def weiger(melding: str, foutcode: str, uitzondering=frappe.ValidationError) -> None:
	"""Weiger met een vaste foutcode naast de melding (`foutcode` in het antwoord): de desktop-app, de gateway en de
	browser kennen zo de reden zonder de tekst te lezen."""
	frappe.local.response["foutcode"] = foutcode
	frappe.throw(melding, uitzondering)


def is_gearchiveerd(ruimte: str | None) -> bool:
	return bool(ruimte) and bool(cint(frappe.db.get_value("Ruimte", ruimte, "gearchiveerd")))


def weiger_als_gearchiveerd(ruimte: str | None) -> None:
	if is_gearchiveerd(ruimte):
		weiger(
			_("Deze ruimte is gearchiveerd en alleen te lezen. Vraag een beheerder om hem terug te zetten."),
			"ruimte_gearchiveerd",
			frappe.PermissionError,
		)


def namens(gebruiker: str | None) -> str | None:
	"""Voor wie een serviceaanroep uit het beheer een handeling uitvoert, alleen voor het auditspoor; de rechten
	blijven die van de echte aanroeper. Telt alleen als de aanroeper Administrator of System Manager is, en alleen
	voor een bestaande, actieve gebruiker; anders wordt het genegeerd (bij een onbekende gebruiker met een
	waarschuwing in het log)."""
	gebruiker = (gebruiker or "").strip()
	if not gebruiker or not is_superbeheer():
		return None
	if not cint(frappe.db.get_value("User", gebruiker, "enabled")):
		frappe.logger("openaec_storage").warning(
			"namens genegeerd: %s is geen bestaande, actieve gebruiker (aanroeper %s)", gebruiker, frappe.session.user
		)
		return None
	return gebruiker


def mag_ruimte_beheren(ruimte: str, gebruiker: str | None = None) -> bool:
	"""Beheerder van de ruimte (of Administrator/System Manager)."""
	return mag(ruimte, "write", gebruiker, ruimte_zelf=True)


def rol_in_ruimte(ruimte: str, gebruiker: str | None = None) -> str | None:
	gebruiker = gebruiker or frappe.session.user
	return frappe.db.get_value(
		"Ruimtelid", {"parent": ruimte, "parenttype": "Ruimte", "gebruiker": gebruiker}, "rol"
	)


def mag(ruimte: str, actie: str, gebruiker: str | None = None, ruimte_zelf: bool = False) -> bool:
	"""Mag `gebruiker` deze actie in de ruimte? `ruimte_zelf`: het gaat om de ruimte-instellingen (leden)."""
	gebruiker = gebruiker or frappe.session.user
	if actie not in LEESACTIES and not ruimte_zelf and is_gearchiveerd(ruimte):
		return False
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
	# Wie de ruimte kan lezen, hoort waarom schrijven niet gaat; een ander alleen dat hij geen toegang heeft.
	if actie not in LEESACTIES and mag(ruimte, "read"):
		weiger_als_gearchiveerd(ruimte)
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
		plek = doc.get("map")
	elif doc.doctype == "Opslagmap":
		plek = doc.name if not doc.is_new() else doc.get("bovenliggende")
	else:
		return mag(ruimte, ptype, user)
	# Een alleen-lezenmap uit een mapsjabloon is ook via het formulier en /api/resource niet te wijzigen.
	if ptype not in LEESACTIES:
		from openaec_storage.mapsjablonen import is_alleen_lezen

		if is_alleen_lezen(ruimte, plek, user):
			return False
	return mag_op_plek(ruimte, plek, ptype, doc.name if doc.doctype == "Opslagbestand" else None, user)


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


def deling_toestand(gebruiker: str, map: str | None, bestand: str | None = None) -> tuple[str | None, str | None]:
	"""Wat een gebruiker via delingen heeft op deze plek (de plek zelf of erboven): het hoogste recht (`schrijven` of
	`lezen`) en, als er geen actieve deling is, waarom niet: `deling_ingetrokken` of `deling_verlopen` (er was er een),
	of None (er was er nooit een)."""
	doelen = [("Opslagmap", m) for m in voorouders(map)]
	if bestand:
		doelen.append(("Opslagbestand", bestand))
	if not doelen:
		return None, None
	beste, reden, nu = None, None, frappe.utils.now_datetime()
	for doctype, doel in doelen:
		for rij in frappe.get_all(
			"Opslagdeling",
			filters={"soort": "gebruiker", "gebruiker": gebruiker, "doel_doctype": doctype, "doel": doel},
			fields=["rechten", "verloopt", "ingetrokken"],
			ignore_permissions=True,
		):
			if cint(rij.ingetrokken):
				reden = reden or "deling_ingetrokken"
			elif rij.verloopt and rij.verloopt < nu:
				reden = "deling_verlopen"
			elif rij.rechten == "schrijven":
				return "schrijven", None
			else:
				beste = "lezen"
	return beste, None if beste else reden


def deling_recht(gebruiker: str, map: str | None, bestand: str | None = None) -> str | None:
	"""Het hoogste recht ('schrijven' of 'lezen') dat een gebruiker via delingen heeft op deze plek."""
	return deling_toestand(gebruiker, map, bestand)[0]


def heeft_deling_in(ruimte: str, gebruiker: str) -> bool:
	"""Heeft de gebruiker een actieve deling (als gebruiker) ergens in deze ruimte?"""
	return bool(
		frappe.db.sql(
			"""select name from `tabOpslagdeling` where ruimte = %s and soort = 'gebruiker' and gebruiker = %s
			and ifnull(ingetrokken, 0) = 0 and (verloopt is null or verloopt >= %s) limit 1""",
			(ruimte, gebruiker, frappe.utils.now_datetime()),
		)
	)


DELINGSMELDINGEN = {
	"alleen_lezen_deling": "Dit is met je gedeeld om te bekijken; wijzigen kan niet",
	"deling_ingetrokken": "Deze deling is ingetrokken; je hebt hier geen toegang meer",
	"deling_verlopen": "Deze deling is verlopen; je hebt hier geen toegang meer",
	"buiten_deling": "Dit valt buiten wat met je gedeeld is",
}


def weiger_deling(foutcode: str) -> None:
	weiger(_(DELINGSMELDINGEN[foutcode]), foutcode, frappe.PermissionError)


def mag_op_plek(ruimte: str, map: str | None, actie: str, bestand: str | None = None, gebruiker: str | None = None) -> bool:
	"""Rechten op een plek in de ruimte: via het lidmaatschap, of via een deling op de plek of erboven."""
	gebruiker = gebruiker or frappe.session.user
	if actie not in LEESACTIES and is_gearchiveerd(ruimte):
		return False
	if mag(ruimte, actie, gebruiker):
		return True
	recht = deling_recht(gebruiker, map, bestand)
	if not recht:
		return False
	return actie in LEESACTIES or recht == "schrijven"


def vereis_op_plek(ruimte: str, map: str | None, actie: str, bestand: str | None = None) -> None:
	"""Weiger als de gebruiker hier niet mag. Wie via een deling bij de ruimte kan, hoort waarom niet: alleen lezen
	(`alleen_lezen_deling`), ingetrokken of verlopen (`deling_ingetrokken`, `deling_verlopen`), of buiten de gedeelde
	map of het gedeelde bestand (`buiten_deling`); een lid van de ruimte en een ander krijgen de gewone melding."""
	if not ruimte or not frappe.db.exists("Ruimte", ruimte):
		frappe.throw(_("Ruimte bestaat niet"), frappe.DoesNotExistError)
	if actie not in LEESACTIES and mag_op_plek(ruimte, map, "read", bestand):
		weiger_als_gearchiveerd(ruimte)
	if mag_op_plek(ruimte, map, actie, bestand):
		return
	gebruiker = frappe.session.user
	if not rol_in_ruimte(ruimte, gebruiker):
		recht, reden = deling_toestand(gebruiker, map, bestand)
		if recht == "lezen" and actie not in LEESACTIES:
			weiger_deling("alleen_lezen_deling")
		if not recht and reden:
			weiger_deling(reden)
		if not recht and heeft_deling_in(ruimte, gebruiker):
			weiger_deling("buiten_deling")
	frappe.throw(_("Geen toegang tot deze plek"), frappe.PermissionError)


def actieve_deling(deling: str | None):
	"""Een deling van de ingelogde gebruiker die nu geldt, om als bovenste map (wortel) te gebruiken. Weigert met
	`deling_onbekend` (bestaat niet of is niet voor jou), `deling_ingetrokken`, `deling_verlopen` of `deling_weg` (de
	map of het bestand is verwijderd)."""
	rij = (
		frappe.db.get_value(
			"Opslagdeling",
			deling,
			["name", "ruimte", "doel_doctype", "doel", "soort", "gebruiker", "rechten", "verloopt", "ingetrokken", "owner"],
			as_dict=True,
		)
		if deling
		else None
	)
	if not rij or rij.soort != "gebruiker" or rij.gebruiker != frappe.session.user:
		weiger(_("Deze deling bestaat niet of is niet met jou gedeeld"), "deling_onbekend", frappe.PermissionError)
	if cint(rij.ingetrokken):
		weiger_deling("deling_ingetrokken")
	if rij.verloopt and rij.verloopt < frappe.utils.now_datetime():
		weiger_deling("deling_verlopen")
	if not frappe.db.exists(rij.doel_doctype, rij.doel) or frappe.db.get_value(rij.doel_doctype, rij.doel, "verwijderd_op"):
		weiger(_("Wat met je gedeeld was, is verwijderd"), "deling_weg", frappe.DoesNotExistError)
	return rij


# --- mapsjablonen: kiezen mag iedereen die is ingelogd, beheren alleen een beheerder van de omgeving -----


def recht_op_sjabloon(doc, ptype=None, user=None, debug=False):
	"""has_permission-hook voor Mapsjabloon."""
	user = user or frappe.session.user
	if user == "Guest":
		return False
	if (ptype or "read") in LEESACTIES:
		return True
	return mag_ruimte_maken(user)


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
