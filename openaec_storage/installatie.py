"""Vaste OAuth-client voor de desktop-app (ontwerp §8): publiek (PKCE, geen geheim), loopback-redirect,
zonder toestemmingsscherm omdat het onze eigen app is. Wordt bij installeren en migreren bijgewerkt.

Ook de standaardmapsjablonen ("Bedrijf" en "Project (BIM)") worden dan aangemaakt als ze er nog niet zijn, en op een
site met ERPNext het veld `project` op Ruimte (zorg_voor_projectveld)."""

import frappe

DESKTOP_CLIENT = "openaec-cloud-desktop"
DESKTOP_REDIRECT = "http://127.0.0.1:47613/callback"


def zorg_voor_desktop_client():
	velden = {
		"app_name": "OpenAEC Cloud (desktop)",
		"skip_authorization": 1,
		"scopes": "all openid",
		"redirect_uris": DESKTOP_REDIRECT,
		"default_redirect_uri": DESKTOP_REDIRECT,
		"grant_type": "Authorization Code",
		"response_type": "Code",
		"token_endpoint_auth_method": "None",
	}
	# Iedere ingelogde gebruiker mag de app gebruiken; wat hij ziet bepalen de ruimterechten.
	rollen = [{"role": "All"}]
	if frappe.db.exists("OAuth Client", DESKTOP_CLIENT):
		doc = frappe.get_doc("OAuth Client", DESKTOP_CLIENT)
		doc.update(velden)
		doc.set("allowed_roles", rollen)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc({"doctype": "OAuth Client", **velden, "allowed_roles": rollen})
		doc.insert(ignore_permissions=True, set_name=DESKTOP_CLIENT)


PROJECTVELD = "Ruimte-project"


def zorg_voor_projectveld():
	"""Het veld `project` op Ruimte (Link naar het ERPNext-Project, optioneel): bij welk project een ruimte hoort, voor
	de projectstrook in Docs en de assistent. Alleen op een site met het doctype Project; Storage zelf hangt alleen
	van Frappe af, dus het veld is een Custom Field en geen deel van ruimte.json."""
	if not frappe.db.exists("DocType", "Project") or frappe.db.exists("Custom Field", PROJECTVELD):
		return
	from frappe.custom.doctype.custom_field.custom_field import create_custom_field

	create_custom_field(
		"Ruimte",
		{
			"fieldname": "project",
			"label": "Project",
			"fieldtype": "Link",
			"options": "Project",
			"insert_after": "soort",
			"in_standard_filter": 1,
			"search_index": 1,
		},
	)


def na_installatie():
	from openaec_storage.mapsjablonen import zorg_voor_standaardsjablonen

	zorg_voor_desktop_client()
	zorg_voor_standaardsjablonen()
	zorg_voor_projectveld()


def na_migratie():
	from openaec_storage.mapsjablonen import zorg_voor_standaardsjablonen

	zorg_voor_desktop_client()
	zorg_voor_standaardsjablonen()
	zorg_voor_projectveld()
