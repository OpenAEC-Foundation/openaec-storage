"""Mapsjablonen: een vaste mappenindeling voor een nieuwe ruimte, met regels per map.

Een Mapsjabloon (per klantsite te beheren) heeft mappen met een pad (`Projecten/Lopend`), een weergavenaam, een
volgorde, een pictogram (naam uit @openaec/icons), toegestane extensies (leeg = alles) en alleen_lezen. Krijgt een
nieuwe ruimte een `mapsjabloon`, dan worden de mappen aangelegd en de regels naar de ruimte gekopieerd
(Ruimtemapregel). De ruimte verandert daarna niet mee als het sjabloon wordt aangepast.

Regels gelden langs elke weg (browser, desktop-app, WebDAV, uploadlink), want ze worden in de API gecontroleerd. Op
een plek geldt de regel van de map zelf, of anders die van de dichtstbijzijnde bovenliggende map met een regel.
- `alleen_lezen`: niets toevoegen, wijzigen, verplaatsen of verwijderen, behalve door een beheerder van de ruimte.
  Foutcode `map_alleen_lezen`.
- `extensies`: alleen bestanden met die extensies (voor iedereen). Foutcode `extensie_niet_toegestaan`.
"""

import os

import frappe
from frappe import _
from frappe.utils import cint

from openaec_storage.rechten import mag_ruimte_beheren, vereis, voorouders, weiger

SOORTEN = ("team", "project", "bedrijf")
# Welke sjablonen passen bij een soort: een bedrijfsmap is een teamruimte, dus bij "team" horen ook de
# bedrijfssjablonen. Er is geen ruimtesoort "bedrijf".
PASSENDE_SOORTEN = {"team": ("team", "bedrijf")}


# --- paden en extensies ------------------------------------------------------------------------------


def splits_pad(pad: str) -> list[str]:
	"""`Projecten/Lopend` -> ["Projecten", "Lopend"]; elke naam volgens dezelfde regels als een map."""
	delen = [d.strip() for d in (pad or "").replace("\\", "/").split("/") if d.strip()]
	if not delen:
		frappe.throw(_("Een map in het sjabloon heeft geen pad"))
	for deel in delen:
		if deel in (".", ".."):
			frappe.throw(_("Ongeldig pad in het sjabloon: {0}").format(pad))
	return delen


def extensies(tekst: str | None) -> list[str]:
	"""Extensies uit tekst met komma's, spaties of regels: `.PDF, dwg` -> ["dwg", "pdf"]. Leeg = alles toegestaan."""
	ruw = (tekst or "").replace(",", " ").replace(";", " ").split()
	return sorted({e.strip().lstrip(".").lower() for e in ruw if e.strip().lstrip(".")})


def _extensie(naam: str) -> str:
	return os.path.splitext(naam or "")[1].lstrip(".").lower()


# --- regels op een plek ------------------------------------------------------------------------------


def _regels(ruimte: str) -> dict:
	return {
		r.map: r
		for r in frappe.get_all(
			"Ruimtemapregel",
			filters={"parent": ruimte, "parenttype": "Ruimte", "map": ("is", "set")},
			fields=["map", "pad", "weergavenaam", "extensies", "alleen_lezen"],
			ignore_permissions=True,
		)
	}


def regel(ruimte: str, map: str | None):
	"""De regel die op deze plek geldt (van de map zelf of de dichtstbijzijnde bovenliggende), of None."""
	if not ruimte or not map:
		return None
	regels = _regels(ruimte)
	if not regels:
		return None
	for m in voorouders(map):
		if m in regels:
			return regels[m]
	return None


def _mapnaam(r) -> str:
	return r.weergavenaam or frappe.db.get_value("Opslagmap", r.map, "naam") or r.pad


def is_alleen_lezen(ruimte: str, map: str | None, gebruiker: str | None = None) -> bool:
	"""Is deze plek alleen te lezen voor `gebruiker`? Een beheerder van de ruimte mag er altijd schrijven."""
	r = regel(ruimte, map)
	return bool(r and cint(r.alleen_lezen)) and not mag_ruimte_beheren(ruimte, gebruiker)


def controleer_schrijven(ruimte: str, map: str | None) -> None:
	"""Weiger als de plek alleen te lezen is (behalve voor een beheerder van de ruimte)."""
	r = regel(ruimte, map)
	if r and cint(r.alleen_lezen) and not mag_ruimte_beheren(ruimte):
		weiger(
			_("De map {0} is alleen te lezen; alleen een beheerder van de ruimte kan hier iets wijzigen").format(
				_mapnaam(r)
			),
			"map_alleen_lezen",
			frappe.PermissionError,
		)


def controleer_boom(ruimte: str, mappen: list[str]) -> None:
	"""Weiger als een van deze mappen (bijv. alle submappen van een map die weg moet) alleen te lezen is."""
	if mag_ruimte_beheren(ruimte):
		return
	for m, r in _regels(ruimte).items():
		if m in mappen and cint(r.alleen_lezen):
			controleer_schrijven(ruimte, m)


def controleer_extensie(ruimte: str, map: str | None, naam: str) -> None:
	"""Weiger een bestandsnaam waarvan de extensie op deze plek niet is toegestaan (geldt voor iedereen)."""
	r = regel(ruimte, map)
	toegestaan = extensies(r.extensies) if r else []
	if toegestaan and _extensie(naam) not in toegestaan:
		weiger(
			_("In de map {0} zijn alleen deze bestandssoorten toegestaan: {1}").format(
				_mapnaam(r), ", ".join("." + e for e in toegestaan)
			),
			"extensie_niet_toegestaan",
		)


def controleer_upload(ruimte: str, map: str | None, naam: str) -> None:
	"""Mag hier een bestand met deze naam komen (nieuw, nieuwe versie of verplaatst)?"""
	controleer_schrijven(ruimte, map)
	controleer_extensie(ruimte, map, naam)


# --- mappen aanleggen bij een nieuwe ruimte ----------------------------------------------------------


def leg_mappen_aan(ruimte) -> None:
	"""Leg de mappen uit het sjabloon aan in de (net aangemaakte) ruimte en kopieer de regels naar de ruimte."""
	from openaec_storage.api import _log

	sjabloon = frappe.get_doc("Mapsjabloon", ruimte.mapsjabloon)
	rijen = sorted(sjabloon.mappen, key=lambda r: (cint(r.volgorde), r.idx))
	mappen: dict[str, str] = {}  # pad in kleine letters -> Opslagmap

	def zorg_voor(delen: list[str]) -> str:
		sleutel = "/".join(delen).lower()
		if sleutel not in mappen:
			bovenliggende = zorg_voor(delen[:-1]) if len(delen) > 1 else None
			doc = frappe.get_doc(
				{"doctype": "Opslagmap", "ruimte": ruimte.name, "naam": delen[-1], "bovenliggende": bovenliggende}
			).insert(ignore_permissions=True)
			_log(ruimte.name, "nieuw", "Opslagmap", doc.name, doc.naam)
			mappen[sleutel] = doc.name
		return mappen[sleutel]

	# Eerst de bovenliggende mappen (kortste paden), dan de regels in de volgorde van het sjabloon.
	for rij in sorted(rijen, key=lambda r: len(splits_pad(r.pad))):
		zorg_voor(splits_pad(rij.pad))
	for rij in rijen:
		delen = splits_pad(rij.pad)
		regel_rij = ruimte.append(
			"mapregels",
			{
				"map": mappen["/".join(delen).lower()],
				"pad": "/".join(delen),
				"weergavenaam": rij.weergavenaam or delen[-1],
				"volgorde": cint(rij.volgorde),
				"pictogram": rij.pictogram,
				"extensies": ", ".join(extensies(rij.extensies)),
				"alleen_lezen": cint(rij.alleen_lezen),
			},
		)
		regel_rij.db_insert()


# --- API ---------------------------------------------------------------------------------------------


def _als_regel(r) -> dict:
	return {
		"map": r.get("map"),
		"pad": r.pad,
		"weergavenaam": r.weergavenaam,
		"volgorde": cint(r.volgorde),
		"pictogram": r.pictogram,
		"extensies": extensies(r.extensies),
		"alleen_lezen": bool(cint(r.alleen_lezen)),
	}


@frappe.whitelist()
def regels(ruimte: str) -> list[dict]:
	"""De mapregels van een ruimte, in volgorde: voor de browser, de desktop-app en het beheer."""
	vereis(ruimte, "read")
	return [
		_als_regel(r)
		for r in frappe.get_all(
			"Ruimtemapregel",
			filters={"parent": ruimte, "parenttype": "Ruimte"},
			fields=["map", "pad", "weergavenaam", "volgorde", "pictogram", "extensies", "alleen_lezen"],
			order_by="idx asc",
			ignore_permissions=True,
		)
	]


@frappe.whitelist()
def sjablonen(soort: str | None = None) -> list[dict]:
	"""De mapsjablonen van deze omgeving, met hun mappen: om er een te kiezen. Met `soort` alleen de passende:
	bij `team` ook de sjablonen van soort `bedrijf` (een bedrijfsmap is een teamruimte), anders die ene soort."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Log eerst in"), frappe.PermissionError)
	filters = {"soort": ("in", PASSENDE_SOORTEN.get(soort, (soort,)))} if soort else {}
	uit = []
	for naam in frappe.get_all("Mapsjabloon", filters=filters, order_by="naam asc", pluck="name", ignore_permissions=True):
		doc = frappe.get_doc("Mapsjabloon", naam)
		uit.append(
			{
				"naam": doc.name,
				"omschrijving": doc.omschrijving,
				"soort": doc.soort,
				"standaard": bool(cint(doc.standaard)),
				"mappen": [_als_regel(r) for r in sorted(doc.mappen, key=lambda r: (cint(r.volgorde), r.idx))],
			}
		)
	return uit


# --- standaardsjablonen (bij installeren en migreren) ------------------------------------------------

# Pad, pictogram, extensies, alleen_lezen. Weergavenaam = laatste deel van het pad, volgorde = positie in de lijst.
STANDAARD = {
	"Bedrijf": {
		"soort": "bedrijf",
		"omschrijving": "Algemene bedrijfsmappen: algemeen, contracten, projecten en de huisstijl.",
		"mappen": [
			("Algemeen", "ui/document", "", 0),
			("Contracten", "ui/license", "", 0),
			("Projecten", "file/project", "", 0),
			("Huisstijl", "insert/image", "", 1),
		],
	},
	"Project (BIM)": {
		"soort": "project",
		"omschrijving": (
			"Bouwproject met BIM: projectinformatie, contract, ontwerp per discipline, modellen in de stappen werk, "
			"gedeeld en gepubliceerd, tekeningen, uitvoering en oplevering."
		),
		"mappen": [
			("00 Projectinformatie", "ui/info", "", 0),
			("01 Contract en financieel", "ui/invoice", "", 0),
			("02 Correspondentie", "ui/mail", "", 0),
			("03 Vergunningen", "ui/license", "", 0),
			("04 Ontwerp", "draw/pencil", "", 0),
			("04 Ontwerp/Architectuur", "draw/pencil", "", 0),
			("04 Ontwerp/Constructie", "draw/pencil", "", 0),
			("04 Ontwerp/Installaties", "draw/pencil", "", 0),
			("05 BIM-modellen", "file/project", "", 0),
			("05 BIM-modellen/Werk", "ui/edit", "", 0),
			("05 BIM-modellen/Gedeeld", "ui/users", "ifc, ifczip, ifcxml, bcf, bcfzip", 0),
			("05 BIM-modellen/Gepubliceerd", "ui/lock", "ifc, ifczip, ifcxml, pdf", 1),
			("06 Tekeningen", "draw/rectangle", "pdf, dwg, dxf", 0),
			("07 Berekeningen", "ui/chart", "", 0),
			("08 Uitvoering", "ui/status", "", 0),
			("09 Oplevering en beheer", "ui/success", "", 0),
			("10 Foto's", "insert/image", "jpg, jpeg, png, heic, webp", 0),
		],
	},
}


def zorg_voor_standaardsjablonen() -> None:
	"""Maak de standaardsjablonen aan als ze er niet zijn. Een bestaand sjabloon (ook een aangepast) blijft zoals het
	is: de sjablonen zijn per klantsite te beheren."""
	for naam, sjabloon in STANDAARD.items():
		if frappe.db.exists("Mapsjabloon", naam):
			continue
		standaard_bestaat = frappe.db.exists("Mapsjabloon", {"soort": sjabloon["soort"], "standaard": 1})
		frappe.get_doc(
			{
				"doctype": "Mapsjabloon",
				"naam": naam,
				"soort": sjabloon["soort"],
				"omschrijving": sjabloon["omschrijving"],
				"standaard": 0 if standaard_bestaat else 1,
				"mappen": [
					{
						"pad": pad,
						"weergavenaam": pad.rsplit("/", 1)[-1],
						"volgorde": (positie + 1) * 10,
						"pictogram": pictogram,
						"extensies": ext,
						"alleen_lezen": alleen_lezen,
					}
					for positie, (pad, pictogram, ext, alleen_lezen) in enumerate(sjabloon["mappen"])
				],
			}
		).insert(ignore_permissions=True)
