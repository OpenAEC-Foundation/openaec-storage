"""Huisstijl van de klant op de openbare pagina's van Storage (deelpagina, aanleveren).

Bron: de huisstijl uit het beheer van de omgeving, via de hook `openaec_huisstijl` (een functie zonder argumenten
die een dict geeft met `naam`, `logo`, `afbeelding` en `kleur`). Staat die er niet of geeft hij niets, dan het
standaardbedrijf (of anders de app-naam, als die niet de standaardnaam van het platform is) met het app-logo en de
bannerafbeelding uit de Website Settings.

Alles wat op de pagina komt, wordt hier nagekeken: een kleur alleen als hex-code, afbeeldingen alleen als pad op
de eigen site (`/files/...`) of https-adres.
"""

import re

import frappe
from frappe.utils import cint, now_datetime

KLEUR = re.compile(r"#(?:[0-9a-fA-F]{3}){1,2}")


def voor_deelpagina() -> dict:
	for pad in frappe.get_hooks("openaec_huisstijl"):
		try:
			gegevens = frappe.get_attr(pad)() or {}
		except Exception:
			frappe.log_error(title=f"Huisstijl uit {pad} niet geladen")
			continue
		if gegevens:
			return _schoon(gegevens)
	return _schoon(_uit_website())


# Standaardnamen van het platform zelf: die zijn geen huisstijl van de klant.
GEEN_KLANTNAAM = {"frappe", "erpnext", "frappe framework"}


def _uit_website() -> dict:
	instellingen = frappe.get_cached_doc("Website Settings")
	app_naam = instellingen.app_name if (instellingen.app_name or "").strip().lower() not in GEEN_KLANTNAAM else None
	return {
		"naam": frappe.defaults.get_global_default("company") or app_naam,
		"logo": instellingen.app_logo,
		"afbeelding": instellingen.banner_image,
	}


def _afbeelding(waarde) -> str | None:
	if not isinstance(waarde, str):
		return None
	waarde = waarde.strip()
	if (waarde.startswith("/") and not waarde.startswith("//")) or waarde.startswith("https://"):
		return waarde
	return None


def _schoon(gegevens: dict) -> dict:
	kleur = gegevens.get("kleur")
	return {
		"naam": str(gegevens.get("naam") or "").strip()[:140] or None,
		"logo": _afbeelding(gegevens.get("logo")),
		"afbeelding": _afbeelding(gegevens.get("afbeelding")),
		"kleur": kleur if isinstance(kleur, str) and KLEUR.fullmatch(kleur) else None,
	}


def schil(context) -> None:
	"""De schil van de openbare pagina's (deelpagina, overdracht): met openaec_web op de site dezelfde als de
	inlogpagina (kop, donker vlak, kaart); zonder die app de gewone webschil van Frappe."""
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
