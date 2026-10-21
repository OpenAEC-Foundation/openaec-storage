app_name = "openaec_storage"
app_title = "OpenAEC Storage"
app_publisher = "OpenAEC Studio BV"
app_description = "Projectbestanden met ruimtes, versies en desktop-sync (bytes in S3)"
app_email = "openaecstudio@gmail.com"
app_license = "agpl-3.0"

required_apps = ["frappe"]

# Toegang tot alle opslag-DocTypes volgt uit het lidmaatschap van de ruimte.
has_permission = {
	"Ruimte": "openaec_storage.rechten.heeft_recht",
	"Opslagmap": "openaec_storage.rechten.heeft_recht",
	"Opslagbestand": "openaec_storage.rechten.heeft_recht",
	"Bestandsversie": "openaec_storage.rechten.heeft_recht",
	"Opslagwijziging": "openaec_storage.rechten.heeft_recht",
	"Opslagvergrendeling": "openaec_storage.rechten.heeft_recht",
	"Opslagdeling": "openaec_storage.rechten.heeft_recht",
}

permission_query_conditions = {
	"Ruimte": "openaec_storage.rechten.query_ruimte",
	"Opslagmap": "openaec_storage.rechten.query_map",
	"Opslagbestand": "openaec_storage.rechten.query_bestand",
	"Opslagwijziging": "openaec_storage.rechten.query_wijziging",
}

# Deellinks zonder account: /delen/<token> (www/delen.html)
website_route_rules = [{"from_route": "/delen/<token>", "to_route": "delen"}]
# De deelpagina geeft 404/410 bij een ongeldige link (Frappe neemt geen statuscode uit de paginacontext over).
after_request = ["openaec_storage.delen.na_verzoek"]
# Storage in de browser (/opslag) voor wie geen desktop-app heeft; ook delingen beheren.
standard_portal_menu_items = [{"title": "Bestanden", "route": "/opslag"}]

after_install = "openaec_storage.installatie.na_installatie"
after_migrate = "openaec_storage.installatie.na_migratie"

# Huisstijl op de deelpagina: een app (bijv. het beheer) kan een functie registreren onder `openaec_huisstijl` die
# naam, logo, afbeelding en kleur geeft; zonder die hook gelden de Website Settings (zie huisstijl.py).
