"""Vaste OAuth-client voor de desktop-app (ontwerp §8): publiek (PKCE, geen geheim), loopback-redirect,
zonder toestemmingsscherm omdat het onze eigen app is. Wordt bij installeren en migreren bijgewerkt.

Ook de standaardmapsjablonen ("Bedrijf" en "Project (BIM)") worden dan aangemaakt als ze er nog niet zijn."""

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


def na_installatie():
	from openaec_storage.mapsjablonen import zorg_voor_standaardsjablonen

	zorg_voor_desktop_client()
	zorg_voor_standaardsjablonen()


def na_migratie():
	from openaec_storage.mapsjablonen import zorg_voor_standaardsjablonen

	zorg_voor_desktop_client()
	zorg_voor_standaardsjablonen()
