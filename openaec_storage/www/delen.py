import frappe

from openaec_storage import huisstijl

no_cache = 1


def get_context(context):
	context.no_breadcrumbs = True
	context.title = "Gedeeld via OpenAEC Storage"
	context.token = frappe.form_dict.get("token") or ""
	context.huisstijl = huisstijl.voor_deelpagina()
