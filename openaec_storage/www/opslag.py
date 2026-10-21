import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/opslag"
		raise frappe.Redirect
	context.no_breadcrumbs = True
	context.title = "OpenAEC Storage"
	context.csrf_token = frappe.sessions.get_csrf_token()
