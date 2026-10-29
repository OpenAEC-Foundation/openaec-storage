"""Projectdossier: documentcodes, disciplines, soorten, fases en revisies op bestanden, en de berekende map Actueel.

Een bestand in een ruimte kan dossiergegevens hebben (velden op Opslagbestand): een **documentcode** (revisies van
hetzelfde document hebben dezelfde code, ook als ze in verschillende bestanden of mappen staan), een **discipline**,
een **soort**, een **fase**, een **revisie** (A, B, C …) en een **status** (concept, ter beoordeling, vrijgegeven,
vervallen). Revisie en status horen bij een versie (Bestandsversie): een nieuwe versie van een vrijgegeven tekening
is weer een concept, terwijl de vrijgegeven versie vrijgegeven blijft. De velden op het bestand zijn die van zijn
huidige versie.

**Actueel** is geen map met kopieën maar een berekening (`actueel`): per documentcode de versie met de hoogste revisie
die vrijgegeven is. Is er van een document nog niets vrijgegeven, dan de nieuwste versie die niet vervallen is, met
`voorlopig: true` (de lijst markeert die duidelijk). Is alles vervallen, dan staat het document er niet in. Een nieuwe
vrijgegeven revisie vervangt dus vanzelf de vorige.

Instelbaar per omgeving (site_config `openaec_dossier`, zie `instellingen`): de fases, disciplines, soorten en de
namen van de vaste mappen. De statussen liggen vast (de berekening van Actueel hangt ervan af).

Rechten: lezen met leesrecht op de plek (lid van de ruimte of een deling), wijzigen met schrijfrecht op de plek, in
een map die niet alleen-lezen is (zoals uploaden). Actueel en het hoofdmodel alleen voor wie de ruimte mag lezen.
"""

import os
import re

import frappe
from frappe import _
from frappe.utils import cint

from openaec_storage.rechten import mag, mag_op_plek, vereis, vereis_op_plek, weiger

STATUSSEN = ("concept", "ter_beoordeling", "vrijgegeven", "vervallen")
VRIJGEGEVEN = "vrijgegeven"
VERVALLEN = "vervallen"
VELDEN = ("documentcode", "discipline", "documentsoort", "fase", "revisie", "revisiestatus")
# Hoe de velden heten in de API (kort) en op het DocType.
API_VELDEN = {
	"documentcode": "documentcode",
	"discipline": "discipline",
	"soort": "documentsoort",
	"fase": "fase",
	"revisie": "revisie",
	"status": "revisiestatus",
}
MODEL_EXTENSIES = ("ifc", "ifczip", "ifcxml")
MAX_BULK = 500
DOSSIERSJABLOON = "Projectdossier"
CODE_PATROON = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,79}$")
REVISIE_PATROON = re.compile(r"^[A-Z0-9]{1,4}$")

# --- instellingen ------------------------------------------------------------------------------------

STANDAARD = {
	"fasen": [
		{"code": "SO", "naam": "SO"},
		{"code": "VO", "naam": "VO"},
		{"code": "DO", "naam": "DO"},
		{"code": "TO", "naam": "TO (bestek)"},
		{"code": "UO", "naam": "UO"},
		{"code": "OPL", "naam": "Oplevering"},
	],
	"disciplines": [
		{"code": "AR", "naam": "Architectuur", "woorden": ["architect", "bouwkund"]},
		{"code": "CO", "naam": "Constructie", "woorden": ["constructie", "construct", "wapening", "fundering"]},
		{"code": "IN", "naam": "Installaties (W/E)", "woorden": ["installatie", "elektra", "werktuig", "w-e", "w/e"]},
		{"code": "IF", "naam": "Infra", "woorden": ["infra", "riolering", "wegontwerp"]},
		{"code": "AL", "naam": "Algemeen", "woorden": ["algemeen"]},
	],
	"soorten": [
		{"code": "TEK", "naam": "Tekening", "extensies": ["pdf", "dwg", "dxf"]},
		{"code": "3D", "naam": "3D-model", "extensies": list(MODEL_EXTENSIES)},
		{"code": "RAP", "naam": "Rapport/berekening", "extensies": ["docx", "doc", "odt", "xlsx", "xls", "ods", "csv"]},
		{"code": "BES", "naam": "Bestek", "extensies": []},
		{"code": "FOT", "naam": "Foto", "extensies": ["jpg", "jpeg", "png", "heic", "heif", "webp"]},
		{"code": "OVR", "naam": "Overig", "extensies": []},
	],
	"mappen": {"fases": "Fases", "model": "3D-model", "fotos": "Foto's", "correspondentie": "Correspondentie"},
}


def _lijst(waarde, standaard: list) -> list[dict]:
	"""Een lijst uit de site_config: elk item met `code` en `naam` (tekst wordt beide)."""
	if not isinstance(waarde, list) or not waarde:
		return [dict(i) for i in standaard]
	uit = []
	for item in waarde:
		if isinstance(item, str):
			item = {"code": item, "naam": item}
		if not isinstance(item, dict) or not str(item.get("code") or "").strip():
			continue
		code = str(item["code"]).strip().upper()
		uit.append({**item, "code": code, "naam": str(item.get("naam") or code)})
	return uit or [dict(i) for i in standaard]


def instellingen() -> dict:
	"""De fases, disciplines, soorten en mapnamen van deze omgeving: de standaard, per onderdeel te vervangen in de
	site_config onder `openaec_dossier` (bijv. `{"fasen": [{"code": "SO", "naam": "Schetsontwerp"}]}`)."""
	eigen = frappe.conf.get("openaec_dossier") or {}
	if not isinstance(eigen, dict):
		eigen = {}
	mappen = {**STANDAARD["mappen"], **{k: v for k, v in (eigen.get("mappen") or {}).items() if isinstance(v, str) and v.strip()}}
	return {
		"fasen": _lijst(eigen.get("fasen"), STANDAARD["fasen"]),
		"disciplines": _lijst(eigen.get("disciplines"), STANDAARD["disciplines"]),
		"soorten": _lijst(eigen.get("soorten"), STANDAARD["soorten"]),
		"statussen": list(STATUSSEN),
		"mappen": mappen,
	}


def mapnaam(naam: str) -> str:
	"""Een naam uit de instellingen als mapnaam: een / of \\ kan niet in een naam (Installaties (W/E) -> (W-E))."""
	return re.sub(r"[<>:\"/\\|?*]", "-", naam).strip(" .") or "-"


def mapvolgorde(ruimte: str | None, bovenliggende: str | None) -> dict[str, int]:
	"""De vaste volgorde van de mappen in een map van een projectdossier, op naam (kleine letters): in de map Fases de
	fases (SO, VO, DO, TO, UO, Oplevering) en in een fase de disciplines, in de volgorde van de instellingen. Leeg
	voor elke andere plek (daar gewoon op naam). Zonder rechtencontrole: alleen voor wie de plek al mag lezen."""
	if not ruimte or not bovenliggende or frappe.db.get_value("Ruimte", ruimte, "soort") != "project":
		return {}
	i = instellingen()
	pad = [p.lower() for p in _mappad(bovenliggende)]
	fases = mapnaam(i["mappen"]["fases"]).lower()
	fasenamen = [mapnaam(f["naam"]).lower() for f in i["fasen"]]
	if pad == [fases]:
		return {naam: n for n, naam in enumerate(fasenamen)}
	if len(pad) == 2 and pad[0] == fases and pad[1] in fasenamen:
		return {mapnaam(d["naam"]).lower(): n for n, d in enumerate(i["disciplines"])}
	return {}


@frappe.whitelist()
def keuzes() -> dict:
	"""De instellingen voor de schermen (keuzelijsten), zonder de zoekwoorden."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Log eerst in"), frappe.PermissionError)
	i = instellingen()
	kort = lambda lijst: [{"code": x["code"], "naam": x["naam"]} for x in lijst]  # noqa: E731
	return {
		"fasen": kort(i["fasen"]),
		"disciplines": kort(i["disciplines"]),
		"soorten": kort(i["soorten"]),
		"statussen": i["statussen"],
		"mappen": i["mappen"],
	}


# --- revisies ----------------------------------------------------------------------------------------


def revisie_sleutel(revisie: str | None) -> tuple:
	"""Volgorde van revisies: geen revisie het laagst, dan cijfers (0, 1, 2 …), dan letters (A … Z, AA …)."""
	r = (revisie or "").strip().upper()
	if not r:
		return (0, 0, "")
	if r.isdigit():
		return (1, int(r), "")
	return (2, len(r), r)


def volgende_revisie(revisie: str | None) -> str:
	"""De revisie na deze: A -> B, Z -> AA, 3 -> 4; zonder revisie A."""
	r = (revisie or "").strip().upper()
	if not r:
		return "A"
	if r.isdigit():
		return str(int(r) + 1)
	if not r.isalpha():
		return "A"
	letters = list(r)
	i = len(letters) - 1
	while i >= 0:
		if letters[i] != "Z":
			letters[i] = chr(ord(letters[i]) + 1)
			return "".join(letters)
		letters[i] = "A"
		i -= 1
	return "A" + "".join(letters)


# --- voorstel uit naam en map ------------------------------------------------------------------------


def _extensie(naam: str) -> str:
	return os.path.splitext(naam or "")[1].lstrip(".").lower()


def _mappad(map: str | None) -> list[str]:
	"""De mapnamen van boven naar beneden."""
	from openaec_storage.rechten import voorouders

	return [frappe.db.get_value("Opslagmap", m, "naam") or "" for m in reversed(voorouders(map))]


def projectcode(ruimte: str) -> str:
	"""Het eerste deel van een documentcode: het project van de ruimte (veld `project`, bijv. PROJ-0004), anders de
	eerste letters van de naam van de ruimte."""
	project = None
	if frappe.get_meta("Ruimte").has_field("project"):
		project = frappe.db.get_value("Ruimte", ruimte, "project")
	if project:
		return re.sub(r"[^A-Z0-9-]", "", str(project).upper())[:24] or "DOC"
	naam = frappe.db.get_value("Ruimte", ruimte, "naam") or ""
	letters = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", naam))[:6].upper()
	return letters or "DOC"


REVISIE_IN_NAAM = re.compile(r"(?:^|[\s_.\-])rev(?:isie)?[\s_.\-]?([A-Za-z]{1,2}|\d{1,3})(?=$|[\s_.\-])", re.IGNORECASE)


def _stam(naam: str) -> str:
	"""De naam zonder extensie en zonder revisieaanduiding: revisies van hetzelfde document hebben dezelfde stam."""
	stam = os.path.splitext(naam or "")[0]
	stam = REVISIE_IN_NAAM.sub("", stam)
	return re.sub(r"[\s_.\-]+$", "", stam).strip().lower()


def _code_in_naam(naam: str, prefix: str) -> str | None:
	stam = os.path.splitext(naam or "")[0].upper()
	treffer = re.search(re.escape(prefix) + r"-[A-Z0-9]{1,4}-[A-Z0-9]{1,4}-\d{2,4}", stam)
	return treffer.group(0) if treffer else None


def _volgnummer(ruimte: str, voorvoegsel: str) -> str:
	"""Het eerste vrije nummer na `<voorvoegsel>-` in deze ruimte (001, 002 …)."""
	bezet = frappe.db.sql(
		"select distinct documentcode from `tabOpslagbestand` where ruimte = %s and documentcode like %s",
		(ruimte, voorvoegsel + "-%"),
	)
	hoogste = 0
	for (code,) in bezet:
		rest = (code or "")[len(voorvoegsel) + 1 :]
		if rest.isdigit():
			hoogste = max(hoogste, int(rest))
	return f"{voorvoegsel}-{hoogste + 1:03d}"


def voorstel_voor(bestand: str, discipline_code: str | None = None, soort_code: str | None = None) -> dict:
	"""Wat de dossiergegevens van een bestand waarschijnlijk zijn, uit de naam en de map: de fase en discipline uit de
	mappen (Fases/DO/Constructie), de soort uit de extensie of de map (3D-model, Foto's), de revisie uit de naam
	(`… rev B.pdf`) en de documentcode uit de naam, van een ander bestand met dezelfde naam (een eerdere revisie), of
	een nieuwe (`<project>-<discipline>-<soort>-<nummer>`)."""
	rij = frappe.db.get_value("Opslagbestand", bestand, ["name", "ruimte", "map", "naam"], as_dict=True)
	i = instellingen()
	paden = [p.lower() for p in _mappad(rij.map)]
	naam_laag = (rij.naam or "").lower()
	ext = _extensie(rij.naam)

	fase = next((f["code"] for f in i["fasen"] if mapnaam(f["naam"]).lower() in paden or f["code"].lower() in paden), None)
	discipline = next((d["code"] for d in i["disciplines"] if mapnaam(d["naam"]).lower() in paden), None)
	if not discipline:
		tokens = set(re.split(r"[\s_.\-]+", os.path.splitext(rij.naam or "")[0].upper()))
		discipline = next((d["code"] for d in i["disciplines"] if d["code"] in tokens), None)
	if not discipline:
		discipline = next(
			(d["code"] for d in i["disciplines"] if any(w in naam_laag for w in d.get("woorden") or [])), None
		)
	soort = None
	if i["mappen"]["model"].lower() in paden and ext in MODEL_EXTENSIES:
		soort = next((s["code"] for s in i["soorten"] if ext in (s.get("extensies") or [])), None)
	if not soort and i["mappen"]["fotos"].lower() in paden:
		soort = next((s["code"] for s in i["soorten"] if ext in (s.get("extensies") or [])), None)
	if not soort and "bestek" in naam_laag:
		soort = "BES" if any(s["code"] == "BES" for s in i["soorten"]) else None
	if not soort:
		soort = next((s["code"] for s in i["soorten"] if ext in (s.get("extensies") or [])), None)
	if not soort:
		soort = "OVR" if any(s["code"] == "OVR" for s in i["soorten"]) else None

	treffer = REVISIE_IN_NAAM.search(os.path.splitext(rij.naam or "")[0])
	revisie = treffer.group(1).upper() if treffer else None

	prefix = projectcode(rij.ruimte)
	code = _code_in_naam(rij.naam, prefix)
	if not code:
		# Een eerdere revisie van hetzelfde document (dezelfde naam zonder de revisie) heeft al een code.
		stam = _stam(rij.naam)
		for ander in frappe.get_all(
			"Opslagbestand",
			filters={"ruimte": rij.ruimte, "name": ("!=", rij.name), "documentcode": ("is", "set"), "verwijderd_op": ("is", "not set")},
			fields=["naam", "documentcode"],
			limit_page_length=0,
			ignore_permissions=True,
		):
			if _stam(ander.naam) == stam:
				code = ander.documentcode
				break
	if not code:
		code = _volgnummer(rij.ruimte, "-".join(x for x in (prefix, discipline_code or discipline or "AL", soort_code or soort or "OVR")))
	return {
		"documentcode": code,
		"discipline": discipline,
		"soort": soort,
		"fase": fase,
		"revisie": revisie or "A",
		"status": "concept",
	}


# --- lezen en wijzigen -------------------------------------------------------------------------------


def _als_api(rij) -> dict:
	return {
		"documentcode": rij.get("documentcode") or None,
		"discipline": rij.get("discipline") or None,
		"soort": rij.get("documentsoort") or None,
		"fase": rij.get("fase") or None,
		"revisie": rij.get("revisie") or None,
		"status": rij.get("revisiestatus") or None,
		"hoofdmodel": bool(cint(rij.get("hoofdmodel"))),
	}


def _bestand(bestand: str, actie: str):
	rij = frappe.db.get_value(
		"Opslagbestand",
		bestand,
		["name", "ruimte", "map", "naam", "huidige_versie", "verwijderd_op", *VELDEN, "hoofdmodel"],
		as_dict=True,
	)
	if not rij or rij.verwijderd_op:
		frappe.throw(_("Bestand bestaat niet"), frappe.DoesNotExistError)
	vereis_op_plek(rij.ruimte, rij.map, actie, rij.name)
	return rij


@frappe.whitelist()
def eigenschappen(bestand: str) -> dict:
	"""De dossiergegevens van een bestand, met een voorstel (uit naam en map) en per versie de revisie en status."""
	rij = _bestand(bestand, "read")
	versies = frappe.get_all(
		"Bestandsversie",
		filters={"bestand": rij.name},
		fields=["volgnummer", "revisie", "revisiestatus", "maker", "creation"],
		order_by="volgnummer desc",
		limit_page_length=0,
		ignore_permissions=True,
	)
	from openaec_storage import mapsjablonen

	return {
		"bestand": rij.name,
		"naam": rij.naam,
		"ruimte": rij.ruimte,
		**_als_api(rij),
		"voorstel": voorstel_voor(rij.name),
		"is_model": _extensie(rij.naam) in MODEL_EXTENSIES,
		"mag_wijzigen": bool(mag_op_plek(rij.ruimte, rij.map, "write", rij.name))
		and not mapsjablonen.is_alleen_lezen(rij.ruimte, rij.map),
		"versies": [
			{
				"volgnummer": cint(v.volgnummer),
				"revisie": v.revisie or None,
				"status": v.revisiestatus or None,
				"maker": v.maker,
				"creation": v.creation,
			}
			for v in versies
		],
	}


def _codes(soort: str) -> set[str]:
	return {x["code"] for x in instellingen()[soort]}


def _schoon(waarden: dict) -> dict:
	"""Controleer en normaliseer de waarden (API-namen naar velden); leeg = weghalen."""
	uit = {}
	for sleutel, waarde in (waarden or {}).items():
		if sleutel not in API_VELDEN:
			continue
		veld = API_VELDEN[sleutel]
		tekst = (str(waarde).strip() if waarde is not None else "")
		if veld in ("discipline", "documentsoort", "fase", "revisie", "documentcode"):
			tekst = tekst.upper()
		if tekst:
			lijst = {"discipline": "disciplines", "documentsoort": "soorten", "fase": "fasen"}.get(veld)
			if lijst and tekst not in _codes(lijst):
				weiger(_("Onbekende waarde voor {0}: {1}").format(sleutel, tekst), "dossier_onbekende_waarde")
			if veld == "revisiestatus" and tekst.lower() not in STATUSSEN:
				weiger(_("Onbekende status: {0}").format(tekst), "dossier_onbekende_waarde")
			if veld == "revisiestatus":
				tekst = tekst.lower()
			if veld == "revisie" and not REVISIE_PATROON.match(tekst):
				weiger(_("Een revisie is een letter of een getal (A, B, C … of 1, 2, 3 …)"), "dossier_ongeldige_revisie")
			if veld == "documentcode" and tekst != "AUTO" and not CODE_PATROON.match(tekst):
				weiger(
					_("Een documentcode bestaat uit letters, cijfers, punten, streepjes en liggende streepjes"),
					"dossier_ongeldige_code",
				)
		uit[veld] = tekst or None
	return uit


def _controleer_uniek(rij, waarden: dict) -> None:
	"""Twee verschillende bestanden met dezelfde documentcode en dezelfde revisie zijn niet uit elkaar te houden."""
	code = waarden.get("documentcode", rij.documentcode)
	revisie = waarden.get("revisie", rij.revisie)
	if not code or not revisie:
		return
	ander = frappe.db.sql(
		"""select b.naam from `tabOpslagbestand` b where b.ruimte = %s and b.name != %s and b.documentcode = %s
		and b.revisie = %s and b.verwijderd_op is null limit 1""",
		(rij.ruimte, rij.name, code, revisie),
	)
	if ander:
		weiger(
			_("{0} revisie {1} bestaat al: {2}").format(code, revisie, ander[0][0]),
			"dossier_revisie_bestaat",
		)


def _zet(rij, waarden: dict) -> dict:
	from openaec_storage import mapsjablonen

	mapsjablonen.controleer_schrijven(rij.ruimte, rij.map)
	waarden = dict(waarden)
	if waarden.get("documentcode") == "AUTO":
		# Een nieuwe code met de discipline en soort die het bestand krijgt (of al heeft), anders die uit het voorstel.
		waarden["documentcode"] = voorstel_voor(
			rij.name, waarden.get("discipline") or rij.discipline, waarden.get("documentsoort") or rij.documentsoort
		)["documentcode"]
	_controleer_uniek(rij, waarden)
	if waarden:
		frappe.db.set_value("Opslagbestand", rij.name, waarden)
	# Revisie en status horen bij de huidige versie.
	versie = {k: waarden[k] for k in ("revisie", "revisiestatus") if k in waarden}
	if versie and cint(rij.huidige_versie):
		frappe.db.set_value(
			"Bestandsversie",
			{"bestand": rij.name, "volgnummer": cint(rij.huidige_versie)},
			versie,
			update_modified=False,
		)
	from openaec_storage.api import _log

	_log(rij.ruimte, "eigenschappen", "Opslagbestand", rij.name, rij.naam)
	nieuw = frappe.db.get_value("Opslagbestand", rij.name, [*VELDEN, "hoofdmodel"], as_dict=True)
	return {"bestand": rij.name, "naam": rij.naam, **_als_api(nieuw)}


def _als_lijst(waarde) -> list:
	if isinstance(waarde, str):
		waarde = frappe.parse_json(waarde) if waarde.strip().startswith(("[", "{")) else [waarde]
	return list(waarde or [])


@frappe.whitelist(methods=["POST"])
def zet_eigenschappen(bestanden=None, waarden=None) -> dict:
	"""Zet dossiergegevens op één of meer bestanden (ook bulk, hoogstens 500). `waarden`: een deel van `documentcode`,
	`discipline`, `soort`, `fase`, `revisie`, `status`; leeg haalt een waarde weg. Een documentcode bij meer dan één
	bestand alleen als `AUTO` (per bestand een voorstel). Revisie en status gelden voor de huidige versie. Alles of
	niets: weigert één bestand, dan verandert er niets (foutcode van dat bestand)."""
	ids = [str(b) for b in _als_lijst(bestanden) if b]
	if not ids:
		frappe.throw(_("Kies minstens één bestand"))
	if len(ids) > MAX_BULK:
		weiger(_("Hoogstens {0} bestanden tegelijk").format(MAX_BULK), "te_groot_voor_batch")
	waarden = _schoon(frappe.parse_json(waarden) if isinstance(waarden, str) else (waarden or {}))
	if not waarden:
		frappe.throw(_("Geef minstens één eigenschap"))
	if len(ids) > 1 and waarden.get("documentcode") not in (None, "AUTO"):
		weiger(_("Eén documentcode hoort bij één document; kies Automatisch voor meer bestanden"), "dossier_code_bulk")
	rijen = [_bestand(b, "write") for b in ids]
	uit = [_zet(rij, waarden) for rij in rijen]
	return {"bestanden": uit, "aantal": len(uit)}


@frappe.whitelist(methods=["POST"])
def nieuwe_revisie(bestand: str, revisie: str | None = None, status: str = "concept") -> dict:
	"""Na het uploaden van een nieuwe versie: maak er een nieuwe revisie van (standaard de volgende na de hoogste
	revisie van dit document, status concept)."""
	rij = _bestand(bestand, "write")
	if not revisie:
		hoogste = rij.revisie
		if rij.documentcode:
			for (r,) in frappe.db.sql(
				"""select distinct v.revisie from `tabBestandsversie` v join `tabOpslagbestand` b on b.name = v.bestand
				where b.ruimte = %s and b.documentcode = %s and b.verwijderd_op is null and ifnull(v.revisie, '') != ''""",
				(rij.ruimte, rij.documentcode),
			):
				if revisie_sleutel(r) > revisie_sleutel(hoogste):
					hoogste = r
		# De huidige versie heeft (na de upload) nog de vorige revisie: de volgende daarna.
		revisie = volgende_revisie(hoogste)
	waarden = _schoon({"revisie": revisie, "status": status or "concept"})
	if not rij.documentcode:
		waarden.setdefault("documentcode", "AUTO")
	return _zet(rij, waarden)


def bij_nieuwe_versie(doc, method=None) -> None:
	"""doc_events Bestandsversie.before_insert: een nieuwe versie van een dossierdocument houdt de revisie, maar is
	weer een concept (de vrijgegeven versie blijft vrijgegeven). Het bestand neemt de status van zijn nieuwe versie."""
	if doc.get("revisie") or doc.get("revisiestatus"):
		return
	rij = frappe.db.get_value("Opslagbestand", doc.bestand, ["documentcode", "revisie", "revisiestatus"], as_dict=True)
	if not rij or not rij.documentcode:
		return
	doc.revisie = rij.revisie
	doc.revisiestatus = "concept"


def bij_bestand_opslaan(doc, method=None) -> None:
	"""doc_events Opslagbestand.validate: revisie en status op het bestand zijn die van zijn huidige versie (na een
	nieuwe versie, zie `bij_nieuwe_versie`)."""
	if not doc.get("documentcode") or not cint(doc.get("huidige_versie")):
		return
	versie = frappe.db.get_value(
		"Bestandsversie", {"bestand": doc.name, "volgnummer": cint(doc.huidige_versie)}, ["revisie", "revisiestatus"], as_dict=True
	)
	if versie and (versie.revisie or versie.revisiestatus):
		doc.revisie = versie.revisie
		doc.revisiestatus = versie.revisiestatus


# --- Actueel -----------------------------------------------------------------------------------------


def _kandidaten(ruimte: str) -> list:
	return frappe.db.sql(
		"""select b.name as bestand, b.naam, b.map, b.documentcode, b.discipline, b.documentsoort, b.fase,
			b.huidige_versie, b.hoofdmodel, v.volgnummer, v.revisie, v.revisiestatus, v.grootte, v.creation, v.maker
		from `tabOpslagbestand` b join `tabBestandsversie` v on v.bestand = b.name
		where b.ruimte = %s and b.verwijderd_op is null and ifnull(b.documentcode, '') != ''""",
		ruimte,
		as_dict=True,
	)


def bereken_actueel(rijen) -> list[dict]:
	"""Per documentcode de hoogste vrijgegeven revisie (bij gelijke revisie de nieuwste versie); zonder vrijgegeven
	versie de nieuwste die niet vervallen is (`voorlopig`). Losse functie zodat de regel zelf te testen is."""
	per_code: dict[str, list] = {}
	for r in rijen:
		per_code.setdefault(r["documentcode"], []).append(r)
	uit = []
	for code, lijst in per_code.items():
		sleutel = lambda r: (revisie_sleutel(r.get("revisie")), cint(r.get("volgnummer")), str(r.get("creation") or ""))  # noqa: E731
		vrij = [r for r in lijst if r.get("revisiestatus") == VRIJGEGEVEN]
		if vrij:
			beste, voorlopig = max(vrij, key=sleutel), False
		else:
			open_ = [r for r in lijst if r.get("revisiestatus") != VERVALLEN]
			if not open_:
				continue
			beste, voorlopig = max(open_, key=sleutel), True
		revisies = sorted({r.get("revisie") for r in lijst if r.get("revisie")}, key=revisie_sleutel)
		uit.append(frappe._dict({**beste, "documentcode": code, "voorlopig": voorlopig, "revisies": revisies}))
	return uit


@frappe.whitelist()
def actueel(ruimte: str, discipline: str | None = None, soort: str | None = None) -> dict:
	"""De map Actueel van een ruimte: per document de laatst vrijgegeven versie, gegroepeerd per discipline en soort.
	Met `discipline` of `soort` alleen die groep. Elk document: bestand, versie, naam, code, revisie, status, fase,
	`voorlopig` (nog niets vrijgegeven) en de revisies die er zijn."""
	vereis(ruimte, "read")
	i = instellingen()
	namen = {
		"discipline": {d["code"]: d["naam"] for d in i["disciplines"]},
		"soort": {s["code"]: s["naam"] for s in i["soorten"]},
		"fase": {f["code"]: f["naam"] for f in i["fasen"]},
	}
	volgorde = {
		"discipline": {d["code"]: n for n, d in enumerate(i["disciplines"])},
		"soort": {s["code"]: n for n, s in enumerate(i["soorten"])},
	}
	documenten = []
	for r in bereken_actueel(_kandidaten(ruimte)):
		if discipline and (r.discipline or "") != discipline.upper():
			continue
		if soort and (r.documentsoort or "") != soort.upper():
			continue
		documenten.append(
			{
				"bestand": r.bestand,
				"naam": r.naam,
				"map": r.map,
				"versie": cint(r.volgnummer),
				"huidige_versie": cint(r.huidige_versie),
				"documentcode": r.documentcode,
				"discipline": r.discipline or None,
				"soort": r.documentsoort or None,
				"fase": r.fase or None,
				"fase_naam": namen["fase"].get(r.fase or "", r.fase or None),
				"revisie": r.revisie or None,
				"status": r.revisiestatus or None,
				"voorlopig": r.voorlopig,
				"revisies": r.revisies,
				"grootte": cint(r.grootte),
				"gewijzigd": r.creation,
				"maker": r.maker,
				"hoofdmodel": bool(cint(r.hoofdmodel)),
			}
		)
	documenten.sort(
		key=lambda d: (
			volgorde["discipline"].get(d["discipline"] or "", 99),
			volgorde["soort"].get(d["soort"] or "", 99),
			d["documentcode"],
		)
	)
	groepen: dict[tuple, dict] = {}
	for d in documenten:
		k = (d["discipline"] or "", d["soort"] or "")
		if k not in groepen:
			groepen[k] = {
				"discipline": d["discipline"],
				"discipline_naam": namen["discipline"].get(d["discipline"] or "", d["discipline"] or None),
				"soort": d["soort"],
				"soort_naam": namen["soort"].get(d["soort"] or "", d["soort"] or None),
				"documenten": [],
			}
		groepen[k]["documenten"].append(d)
	return {
		"ruimte": ruimte,
		"naam": frappe.db.get_value("Ruimte", ruimte, "naam"),
		"groepen": list(groepen.values()),
		"aantal": len(documenten),
		"voorlopig": sum(1 for d in documenten if d["voorlopig"]),
	}


@frappe.whitelist()
def overzicht(ruimte: str) -> dict:
	"""Voor de mapboom: is dit een projectdossier (een projectruimte), hoeveel documenten staan er in Actueel per
	discipline, en het hoofdmodel."""
	vereis(ruimte, "read")
	soort = frappe.db.get_value("Ruimte", ruimte, "soort")
	if soort != "project":
		return {"dossier": False}
	i = instellingen()
	tellingen: dict[str, int] = {}
	for r in bereken_actueel(_kandidaten(ruimte)):
		tellingen[r.discipline or ""] = tellingen.get(r.discipline or "", 0) + 1
	disciplines = [
		{"code": d["code"], "naam": d["naam"], "aantal": tellingen[d["code"]]} for d in i["disciplines"] if tellingen.get(d["code"])
	]
	if tellingen.get(""):
		disciplines.append({"code": None, "naam": None, "aantal": tellingen[""]})
	return {
		"dossier": True,
		"aantal": sum(tellingen.values()),
		"disciplines": disciplines,
		"hoofdmodel": _hoofdmodel(ruimte),
	}


# --- hoofdmodel --------------------------------------------------------------------------------------


def _hoofdmodel(ruimte: str) -> dict | None:
	"""Het hoofdmodel van het project: het aangewezen model zonder discipline (of Algemeen), anders het eerste
	aangewezen model. De versie is die uit Actueel (de laatst vrijgegeven) als het model een documentcode heeft."""
	modellen = frappe.get_all(
		"Opslagbestand",
		filters={"ruimte": ruimte, "hoofdmodel": 1, "verwijderd_op": ("is", "not set")},
		fields=["name", "naam", "map", "discipline", "documentcode", "huidige_versie", "revisie", "revisiestatus"],
		order_by="modified desc",
		limit_page_length=0,
		ignore_permissions=True,
	)
	if not modellen:
		return None
	actueel_per_code = {r.documentcode: r for r in bereken_actueel(_kandidaten(ruimte))}

	def als_model(m) -> dict:
		a = actueel_per_code.get(m.documentcode) if m.documentcode else None
		return {
			"bestand": m.name,
			"naam": m.naam,
			"map": m.map,
			"discipline": m.discipline or None,
			"versie": cint(a.volgnummer) if a and a.bestand == m.name else cint(m.huidige_versie),
			"revisie": (a.revisie if a and a.bestand == m.name else m.revisie) or None,
			"status": (a.revisiestatus if a and a.bestand == m.name else m.revisiestatus) or None,
			"voorlopig": bool(a.voorlopig) if a and a.bestand == m.name else (m.revisiestatus != VRIJGEGEVEN),
		}

	hoofd = next((m for m in modellen if not m.discipline or m.discipline == "AL"), modellen[0])
	return {**als_model(hoofd), "per_discipline": [als_model(m) for m in modellen]}


@frappe.whitelist()
def hoofdmodel(ruimte: str) -> dict:
	"""Het hoofdmodel van een projectruimte (of `{model: null}`)."""
	vereis(ruimte, "read")
	return {"model": _hoofdmodel(ruimte)}


@frappe.whitelist(methods=["POST"])
def zet_hoofdmodel(bestand: str, aan=1) -> dict:
	"""Wijs een IFC-model aan als hoofdmodel van het project (per discipline één: een ander model met dezelfde
	discipline is het daarna niet meer), of haal dat weg. Met schrijfrecht op de plek."""
	rij = _bestand(bestand, "write")
	if cint(aan) and _extensie(rij.naam) not in MODEL_EXTENSIES:
		weiger(_("Alleen een IFC-model kan het hoofdmodel zijn"), "dossier_geen_model")
	if cint(aan):
		for ander in frappe.get_all(
			"Opslagbestand",
			filters={"ruimte": rij.ruimte, "hoofdmodel": 1, "name": ("!=", rij.name), "discipline": rij.discipline or ("is", "not set")},
			pluck="name",
			ignore_permissions=True,
		):
			frappe.db.set_value("Opslagbestand", ander, "hoofdmodel", 0)
	frappe.db.set_value("Opslagbestand", rij.name, "hoofdmodel", 1 if cint(aan) else 0)
	from openaec_storage.api import _log

	_log(rij.ruimte, "eigenschappen", "Opslagbestand", rij.name, rij.naam)
	return {"bestand": rij.name, "hoofdmodel": bool(cint(aan)), "model": _hoofdmodel(rij.ruimte)}


# --- mappenstructuur ---------------------------------------------------------------------------------


def dossiermappen() -> list[tuple[str, str, str, int]]:
	"""De mappen van een projectdossier (pad, pictogram, extensies, alleen_lezen): Fases/<fase>/<discipline>, het
	3D-model, de foto's en de correspondentie. Actueel is geen map maar een berekening (`actueel`)."""
	i = instellingen()
	m = i["mappen"]
	fasen = mapnaam(m["fases"])
	uit = [(fasen, "ui/status", "", 0)]
	for fase in i["fasen"]:
		uit.append((f"{fasen}/{mapnaam(fase['naam'])}", "ui/status", "", 0))
		for d in i["disciplines"]:
			uit.append((f"{fasen}/{mapnaam(fase['naam'])}/{mapnaam(d['naam'])}", "draw/pencil", "", 0))
	foto_ext = next((s.get("extensies") for s in i["soorten"] if s["code"] == "FOT"), None) or []
	uit += [
		(mapnaam(m["model"]), "file/project", "", 0),
		(mapnaam(m["fotos"]), "insert/image", ", ".join(foto_ext), 0),
		(mapnaam(m["correspondentie"]), "ui/mail", "", 0),
	]
	return uit


def zorg_voor_dossiersjabloon() -> None:
	"""Het mapsjabloon "Projectdossier" (bij installeren en migreren). Komt het er nu voor het eerst, dan wordt het de
	standaard voor nieuwe projectruimtes (site_config `openaec_dossier_standaard: 0` houdt de huidige standaard).
	Een bestaand sjabloon (ook een aangepast) blijft zoals het is."""
	if frappe.db.exists("Mapsjabloon", DOSSIERSJABLOON):
		return
	standaard = cint(frappe.conf.get("openaec_dossier_standaard", 1))
	if standaard:
		for naam in frappe.get_all("Mapsjabloon", filters={"soort": "project", "standaard": 1}, pluck="name"):
			frappe.db.set_value("Mapsjabloon", naam, "standaard", 0)
	frappe.get_doc(
		{
			"doctype": "Mapsjabloon",
			"naam": DOSSIERSJABLOON,
			"soort": "project",
			"omschrijving": (
				"Projectdossier: Actueel (berekend uit de revisies), fases met per fase de disciplines, het 3D-model, "
				"foto's en correspondentie."
			),
			"standaard": 1 if standaard or not frappe.db.exists("Mapsjabloon", {"soort": "project", "standaard": 1}) else 0,
			"mappen": [
				{
					"pad": pad,
					"weergavenaam": pad.rsplit("/", 1)[-1],
					"volgorde": (positie + 1) * 10,
					"pictogram": pictogram,
					"extensies": ext,
					"alleen_lezen": alleen_lezen,
				}
				for positie, (pad, pictogram, ext, alleen_lezen) in enumerate(dossiermappen())
			],
		}
	).insert(ignore_permissions=True)


def map_op_pad(ruimte: str, pad: list[str], maak: bool = True) -> str | None:
	"""De map op dit pad in de ruimte (op naam, zonder hoofdlettergevoeligheid); ontbrekende mappen maken als `maak`.
	Zonder rechtencontrole: alleen voor de server zelf (spiegelen van projectbijlagen, demo)."""
	from openaec_storage.api import _log

	bovenliggende = None
	for naam in pad:
		bestaand = frappe.db.sql(
			"""select name from `tabOpslagmap` where ruimte = %s and ifnull(bovenliggende, '') = %s
			and lower(naam) = lower(%s) and verwijderd_op is null limit 1""",
			(ruimte, bovenliggende or "", naam),
		)
		if bestaand:
			bovenliggende = bestaand[0][0]
			continue
		if not maak:
			return None
		doc = frappe.get_doc({"doctype": "Opslagmap", "ruimte": ruimte, "naam": naam, "bovenliggende": bovenliggende}).insert(
			ignore_permissions=True
		)
		_log(ruimte, "nieuw", "Opslagmap", doc.name, naam)
		bovenliggende = doc.name
	return bovenliggende


def bewaar_bytes(
	ruimte: str, map: str | None, naam: str, inhoud: bytes, bron: str = "api", controleer_rechten: bool = True,
	herkomst: str | None = None,
) -> dict:
	"""Leg bytes vast als bestand (of nieuwe versie van het bestand met die naam op die plek), zonder upload door een
	browser: voor het spiegelen van projectbijlagen en de demo. Mapregels, naam en quotum gelden altijd; de rechten
	van de aanroeper alleen met `controleer_rechten` (het spiegelen is een handeling van de server zelf, na de
	rechtencontrole op het project). Maker is de ingelogde gebruiker."""
	from openaec_storage import api, mapsjablonen, s3

	if controleer_rechten:
		vereis_op_plek(ruimte, map, "write", api._bestaand_id(ruimte, map, naam))
	naam = api._controleer_naam(naam, ruimte, map)
	mapsjablonen.controleer_upload(ruimte, map, naam)
	api._controleer_quotum(len(inhoud), ruimte)
	bestaand = api._actief_bestand(ruimte, map, naam)
	sleutel = s3.nieuwe_sleutel()
	s3.zet(sleutel, inhoud)
	try:
		uit = api._registreer(
			{
				"ruimte": ruimte,
				"map": map,
				"naam": naam,
				"grootte": len(inhoud),
				"basis_versie": cint(bestaand.huidige_versie) if bestaand else None,
				"sha256": None,
				"bron": bron if bron in api.BRONNEN else "api",
				"sleutel": sleutel,
				"blokgrootte": s3.blokgrootte(len(inhoud)),
				"blokhashes": None,
			}
		)
	except Exception:
		s3.verwijder(sleutel)
		raise
	if herkomst:
		frappe.db.set_value("Opslagbestand", uit["bestand"], "herkomst", herkomst[:140], update_modified=False)
	return uit


def kan_lezen(ruimte: str) -> bool:
	return bool(ruimte) and mag(ruimte, "read")
