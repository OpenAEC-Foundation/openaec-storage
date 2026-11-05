"""Eén keer bij de invoering van stap 4 (ontwerp samenwerken §5.5, "Eén keer bij de invoering"): elk bestand krijgt
voor zijn huidige versie een nummer N dat het nooit eerder uitgaf, en `huidige_versie`, `hoogste_versie` en de
`basisgrens` worden N.

Waarom: vóór stap 4 kon een paar (bestand, versie) al twee inhouden krijgen, langs meer dan één weg. Een bron van
`verplaats` met overschrijven zakte een nummer, werd hersteld en gaf dat nummer opnieuw uit. En twee verplaatsingen
tegelijk naar hetzelfde doel lazen het doel zonder slot en gaven allebei hetzelfde volgende nummer uit (de
WebDAV-gateway verplaatst zo bij elk opslaan via een tijdelijk bestand). Welke paren dat raakte, is achteraf niet
vast te stellen. Daarom is na de patch elke basis onder de grens onzeker (`api._onzekere_basis`), en is versie N een
zekere basis: wie na de patch ophaalt, krijgt N en kan gewoon opslaan.

N = het aantal versieregels van het bestand + het aantal regels `verwijderd` voor het bestand in de wijzigingenlijst
+ 1, en nooit lager dan `huidige_versie + 1` of `hoogste_versie + 1`. Waarom dat boven elk uitgegeven nummer ligt:
elk nieuw nummer was één hoger dan het nummer van een bestaande regel (of 0), dus het hoogste nummer ooit is
hoogstens het aantal uitgiften. Elke uitgifte hing één regel aan het bestand. Een regel ging er alleen af met een
verplaatsing met overschrijven, en die logt altijd `verwijderd` voor de bron (sinds die route bestaat, 829eb23). De
wijzigingenlijst wordt nergens opgeruimd. Een gewone verwijdering logt ook `verwijderd`; dat telt te veel, en een
overgeslagen nummer kan geen kwaad.

Per bestand met minstens één versie:
- De regel op het huidige nummer krijgt volgnummer N: dezelfde regel, dezelfde S3-sleutel, geen kopie; het quotum
  (de som van `grootte`) blijft gelijk. Staan er twee of meer regels op het huidige nummer, dan krijgt de laatst
  gewijzigde N; de andere blijven in de geschiedenis.
- `huidige_versie`, `hoogste_versie` en `basisgrens` worden N.
- Een overdracht (Overdrachtbestand) die naar het oude huidige nummer van het bestand wijst, krijgt N.
Een bestand zonder versies (`huidige_versie` 0) slaat de patch over: grens 0.

Herhalen: de patch draait één keer (patches.txt). Draait hij toch nog eens (bijv. met de hand), dan doet hij niets: na
de eerste keer staat een merkteken in de standaardwaarden van de site (`MERKTEKEN`). Zonder dat merkteken zou een
tweede keer ook bestanden raken die pas na de eerste keer hun eerste versie kregen (die hebben grens 0); dat zou
veilig zijn (N ligt boven elk uitgegeven nummer), maar clients zouden die bestanden dan nog eens als nieuwe versie
zien. Bestanden met een grens slaat de patch ook zonder merkteken over."""

import frappe
from frappe.utils import cint, now

MERKTEKEN = "openaec_storage_versienummers_bij_invoering"


def execute():
	if frappe.db.get_global(MERKTEKEN):
		return
	frappe.db.set_global(MERKTEKEN, now())
	bestanden = frappe.db.sql(
		"""select b.name, b.huidige_versie, b.hoogste_versie, coalesce(v.aantal, 0) as versies,
			coalesce(w.aantal, 0) as verwijderd
		from `tabOpslagbestand` b
		left join (select bestand, count(*) as aantal from `tabBestandsversie` group by bestand) v on v.bestand = b.name
		left join (
			select doel, count(*) as aantal from `tabOpslagwijziging`
			where doel_doctype = 'Opslagbestand' and soort = 'verwijderd'
			group by doel
		) w on w.doel = b.name
		where b.huidige_versie > 0 and b.basisgrens = 0""",
		as_dict=True,
	)
	if not bestanden:
		return
	# De regel op het huidige nummer, per bestand; bij twee of meer de laatst gewijzigde.
	huidige_regel = {}
	for naam, bestand in frappe.db.sql(
		"""select v.name, v.bestand from `tabBestandsversie` v
		join `tabOpslagbestand` b on b.name = v.bestand and v.volgnummer = b.huidige_versie
		where b.huidige_versie > 0 and b.basisgrens = 0
		order by v.bestand, v.modified, v.name"""
	):
		huidige_regel[bestand] = naam  # de laatste in de volgorde wint
	nieuw = {}
	for b in bestanden:
		n = max(cint(b.versies) + cint(b.verwijderd) + 1, cint(b.huidige_versie) + 1, cint(b.hoogste_versie) + 1)
		nieuw[(b.name, cint(b.huidige_versie))] = n
		if b.name in huidige_regel:
			frappe.db.sql("update `tabBestandsversie` set volgnummer = %s where name = %s", (n, huidige_regel[b.name]))
			frappe.db.sql(
				"update `tabOpslagbestand` set huidige_versie = %s, hoogste_versie = %s, basisgrens = %s where name = %s",
				(n, n, n, b.name),
			)
		else:
			# Geen regel op het huidige nummer (dat hoort niet voor te komen): de huidige versie wijst al naar niets.
			# Alleen het hoogste nummer en de grens; dan is ook het oude nummer geen zekere basis meer.
			frappe.db.sql(
				"update `tabOpslagbestand` set hoogste_versie = %s, basisgrens = %s where name = %s", (n, n, b.name)
			)
	for naam, bron, versie in frappe.db.sql(
		"""select name, bron_bestand, bron_versie from `tabOverdrachtbestand`
		where ifnull(bron_bestand, '') != ''"""
	):
		n = nieuw.get((bron, cint(versie)))
		if n and bron in huidige_regel:
			frappe.db.sql("update `tabOverdrachtbestand` set bron_versie = %s where name = %s", (n, naam))
