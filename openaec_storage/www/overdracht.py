"""Ontvangstpagina `/overdracht/<token>`: de bestanden van een overdracht, in de huisstijl van de klant.

Zelfde schil en opbouw als de deelpagina (`/delen`). De stand van de link wordt hier al bepaald: onbekend geeft 404,
verlopen of ingetrokken 410, met een duidelijke melding. De bestanden haalt de pagina daarna op via
`openaec_storage.overdracht` (openen, download, zip).
"""

import frappe
from frappe import _
from frappe.utils import format_datetime, get_fullname

from openaec_storage import huisstijl, overdracht

no_cache = 1

FOUTEN = {
	"onbekend": (
		404,
		"Deze link werkt niet (meer)",
		"De link bestaat niet. Controleer het adres of vraag de afzender om een nieuwe link.",
	),
	"verlopen": (410, "Deze overdracht is verlopen", "De bestanden waren beschikbaar tot {0}. Vraag de afzender ze opnieuw te sturen."),
	"ingetrokken": (
		410,
		"Deze overdracht is ingetrokken",
		"De afzender heeft de bestanden teruggetrokken. Vraag de afzender om een nieuwe link.",
	),
}


def get_context(context):
	context.no_breadcrumbs = True
	context.token = frappe.form_dict.get("token") or ""
	context.huisstijl = huisstijl.voor_deelpagina()

	stand, rij = overdracht.stand(context.token)
	context.stand = stand
	eigenaar = rij.owner if rij and rij.owner not in ("Guest", "Administrator") else None
	context.afzender = get_fullname(eigenaar) if eigenaar else None
	context.lead = None
	if stand in FOUTEN:
		status, titel, tekst = FOUTEN[stand]
		frappe.local.openaec_status = status
		context.fout_titel = _(titel)
		context.fout_tekst = _(tekst).format(
			format_datetime(rij.verloopt_op, "d MMMM yyyy HH:mm") if rij and rij.verloopt_op else ""
		)
		context.kop = _("Bestanden ontvangen")
	else:
		context.kop = _("Er zijn bestanden voor je verstuurd")
		if context.afzender:
			context.lead = _("Verstuurd door {0}").format(context.afzender)
	paginatitel = context.get("fout_titel") or context.kop
	context.title = f"{paginatitel} · {context.huisstijl['naam']}" if context.huisstijl.get("naam") else paginatitel
	# Ingelogde bezoekers (bijv. de afzender die zijn eigen link test) sturen het CSRF-token mee.
	context.csrf = frappe.sessions.get_csrf_token() if frappe.session.user != "Guest" else ""
	huisstijl.schil(context)
	return context
