"""Deelpagina `/delen/<token>`: wat achter een deellink zit, in de huisstijl van de klant.

De stand van de link (geldig, wachtwoord nodig, onbekend, verlopen, verwijderd) wordt hier al bepaald: een ongeldige
link geeft meteen een duidelijke melding met 404/410, zonder dat de pagina eerst iets probeert te laden. De inhoud
zelf (bestand, map, aanleveren) haalt de pagina daarna op via `openaec_storage.delen`.

Met openaec_web op de site krijgt de pagina dezelfde schil als de inlogpagina (kop, donker vlak, kaart); zonder die
app de gewone webschil van Frappe.
"""

import frappe
from frappe import _
from frappe.utils import cint, format_date, get_fullname, now_datetime

from openaec_storage import delen, huisstijl

no_cache = 1

FOUTEN = {
	"onbekend": (
		404,
		"Deze link werkt niet (meer)",
		"De link bestaat niet of is ingetrokken. Vraag de afzender om een nieuwe link.",
	),
	"verlopen": (410, "Deze link is verlopen", "De link was geldig tot {0}. Vraag de afzender om een nieuwe link."),
	"weg": (
		410,
		"Niet meer beschikbaar",
		"Het gedeelde bestand of de gedeelde map is verwijderd. Vraag de afzender om een nieuwe link.",
	),
}


def get_context(context):
	context.no_breadcrumbs = True
	context.token = frappe.form_dict.get("token") or ""
	context.huisstijl = huisstijl.voor_deelpagina()

	stand, deling = delen.stand(context.token)
	context.stand = stand
	context.soort = None
	context.gedeeld_door = None
	context.lead = None
	if deling:
		context.soort = "uploaden" if deling.rechten == "uploaden" else ("map" if deling.doel_doctype == "Opslagmap" else "bestand")
		eigenaar = deling.owner if deling.owner not in ("Guest", "Administrator") else None
		context.gedeeld_door = get_fullname(eigenaar) if eigenaar else None

	if stand in FOUTEN:
		status, titel, tekst = FOUTEN[stand]
		frappe.local.openaec_status = status
		context.fout_titel = _(titel)
		context.fout_tekst = _(tekst).format(format_date(deling.verloopt) if deling and deling.verloopt else "")
		# De melding staat in de kaart; het donkere vlak blijft neutraal (niet twee keer dezelfde kop).
		context.kop = _("Gedeelde link")
	else:
		context.kop = {
			"uploaden": _("Lever je bestanden aan"),
			"map": _("Er is een map met je gedeeld"),
		}.get(context.soort, _("Er is een bestand met je gedeeld"))
		if context.gedeeld_door:
			sjabloon = _("Gevraagd door {0}") if context.soort == "uploaden" else _("Gedeeld door {0}")
			context.lead = sjabloon.format(context.gedeeld_door)
	paginatitel = context.get("fout_titel") or context.kop
	context.title = f"{paginatitel} · {context.huisstijl['naam']}" if context.huisstijl.get("naam") else paginatitel

	context.oa_schil = "openaec_web" in frappe.get_installed_apps()
	if context.oa_schil:
		# Losjes gekoppeld: een oudere openaec_web zonder een van deze functies breekt de pagina niet.
		from openaec_web.www import login as oa_login

		aanmelden_url = getattr(oa_login, "aanmelden_url", None)
		context.body_class = "oa-schil"
		context.oa_cloud_url = oa_login.site_url() if hasattr(oa_login, "site_url") else ""
		context.oa_aanmelden_url = aanmelden_url() if aanmelden_url else None
		context.oa_jaar = now_datetime().year
		context.oa_ontwikkelmodus = bool(cint(frappe.conf.get("developer_mode")))
	return context
