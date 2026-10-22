import frappe

from openaec_storage.rechten import mag_ruimte_maken

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/opslag"
		raise frappe.Redirect
	context.no_breadcrumbs = True
	context.title = "OpenAEC Storage"
	context.csrf_token = frappe.sessions.get_csrf_token()
	# Lege toestand (nog geen ruimte): een beheerder van de omgeving krijgt de weg naar het beheer, een ander de
	# vraag om de beheerder. Dezelfde regel als bij het aanmaken; de browser leidt geen rechten af.
	context.mag_ruimte_maken = mag_ruimte_maken()
