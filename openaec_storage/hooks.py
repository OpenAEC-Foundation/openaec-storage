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
	# Mapsjablonen: kiezen mag iedereen die is ingelogd, beheren alleen een beheerder van de omgeving.
	"Mapsjabloon": "openaec_storage.rechten.recht_op_sjabloon",
	# Overdrachten staan los van de ruimtes: alleen de maker (en het beheer).
	"Overdracht": "openaec_storage.rechten.recht_op_overdracht",
	"Overdrachtbestand": "openaec_storage.rechten.recht_op_overdracht",
}

permission_query_conditions = {
	"Ruimte": "openaec_storage.rechten.query_ruimte",
	"Opslagmap": "openaec_storage.rechten.query_map",
	"Opslagbestand": "openaec_storage.rechten.query_bestand",
	"Opslagwijziging": "openaec_storage.rechten.query_wijziging",
	"Overdracht": "openaec_storage.rechten.query_overdracht",
}

# Deellinks zonder account: /delen/<token> (www/delen.html)
website_route_rules = [
	{"from_route": "/delen/<token>", "to_route": "delen"},
	# Ontvangstpagina van een overdracht (www/overdracht.html)
	{"from_route": "/overdracht/<token>", "to_route": "overdracht"},
]
# De deelpagina en de ontvangstpagina geven 404/410 bij een ongeldige link (Frappe neemt geen statuscode uit de paginacontext over).
after_request = ["openaec_storage.delen.na_verzoek"]
# Storage in de browser (/opslag) voor wie geen desktop-app heeft; ook delingen beheren.
standard_portal_menu_items = [{"title": "Bestanden", "route": "/opslag"}]

# Verlopen overdrachten en blijven liggende concepten opruimen (objecten uit S3, record naar Verlopen).
scheduler_events = {"hourly": ["openaec_storage.overdracht.opruimen"]}

# Projectdossier (dossier.py): revisie en status per versie; het bestand volgt zijn huidige versie.
doc_events = {
	"Bestandsversie": {"before_insert": "openaec_storage.dossier.bij_nieuwe_versie"},
	"Opslagbestand": {"validate": "openaec_storage.dossier.bij_bestand_opslaan"},
}

after_install = "openaec_storage.installatie.na_installatie"
after_migrate = "openaec_storage.installatie.na_migratie"

# Huisstijl op de deelpagina: een app (bijv. het beheer) kan een functie registreren onder `openaec_huisstijl` die
# naam, logo, afbeelding en kleur geeft; zonder die hook gelden de Website Settings (zie huisstijl.py).
