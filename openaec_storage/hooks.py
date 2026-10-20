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
