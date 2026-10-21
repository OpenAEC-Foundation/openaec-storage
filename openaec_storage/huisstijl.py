"""Huisstijl van de klant op de openbare pagina's van Storage (deelpagina, aanleveren).

Bron: de huisstijl uit het beheer van de omgeving, via de hook `openaec_huisstijl` (een functie zonder argumenten
die een dict geeft met `naam`, `logo`, `afbeelding` en `kleur`). Staat die er niet of geeft hij niets, dan de
Website Settings van de site (app-naam, app-logo, bannerafbeelding) en anders het standaardbedrijf.

Alles wat op de pagina komt, wordt hier nagekeken: een kleur alleen als hex-code, afbeeldingen alleen als pad op
de eigen site (`/files/...`) of https-adres.
"""

import re

import frappe

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


def _uit_website() -> dict:
	instellingen = frappe.get_cached_doc("Website Settings")
	return {
		"naam": instellingen.app_name or frappe.defaults.get_global_default("company"),
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
