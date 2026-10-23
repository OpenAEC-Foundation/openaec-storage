"""Geldige namen voor bestanden en mappen: dezelfde regels als de desktop-app (crate openaec-sync, `namen.rs`).

Een naam die op Windows niet kan, komt er niet in: daar is zo'n bestand met normale paden niet te openen, te
verplaatsen of te verwijderen, en een sync-programma blijft er dan op vastlopen. Hulpprogramma's maken ze vaak per
ongeluk (een shell-omleiding als `2>nul` in een Unix-shell op Windows geeft een bestand dat letterlijk `nul` heet).

Ongeldig zijn:
- de apparaatnamen CON, PRN, AUX, NUL, CONIN$, CONOUT$, COM0-9 en LPT0-9 (ook met de superscriptcijfers ¹²³),
  zonder onderscheid tussen hoofd- en kleine letters en ook met een extensie (`nul.txt`, `con.log`);
- namen die op een punt of spatie eindigen;
- de tekens < > : " / \\ | ? * en controletekens;
- namen langer dan 255 tekens, en een lege naam, `.` of `..`.
"""

from frappe import _

MAX_NAAM = 255
VERBODEN_TEKENS = frozenset('<>:"/\\|?*')
CIJFERS = "0123456789¹²³"
APPARATEN = frozenset(
	{"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
	| {f"COM{c}" for c in CIJFERS}
	| {f"LPT{c}" for c in CIJFERS}
)


def is_gereserveerd(naam: str) -> bool:
	"""Is dit (het deel voor de eerste punt, zonder spaties aan het eind) een apparaatnaam van Windows?"""
	return naam.split(".", 1)[0].rstrip(" ").upper() in APPARATEN


def ongeldige_naam(naam: str | None) -> str | None:
	"""De reden waarom deze naam niet kan, of None als hij goed is."""
	if naam is None or naam in ("", ".", ".."):
		return _("de naam is leeg")
	if len(naam) > MAX_NAAM:
		return _("de naam is langer dan {0} tekens").format(MAX_NAAM)
	for teken in naam:
		if teken in VERBODEN_TEKENS:
			return _("het teken {0} kan niet in een naam").format(teken)
		if ord(teken) < 32:
			return _("een naam kan geen controleteken bevatten")
	if naam[-1] in ". ":
		return _("een naam kan niet op een punt of spatie eindigen")
	if is_gereserveerd(naam):
		return _("{0} is een gereserveerde naam op Windows").format(naam.split(".", 1)[0].rstrip(" "))
	return None
