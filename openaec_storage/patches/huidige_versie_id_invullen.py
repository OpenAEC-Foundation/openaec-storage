"""Invoering van `Opslagbestand.huidige_versie_id` (ontwerp samenwerken §5.5, "De basis is de versieregel zelf, niet
haar nummer"): per bestand het id van de versieregel die nu de inhoud is. Er wordt niets hernummerd als er niets mis
is, en er komt geen kopie in S3.

Per bestand:
- De huidige versieregel is de regel met `volgnummer = huidige_versie`.
- Staan er twee of meer regels op hetzelfde nummer (twee verplaatsingen met overschrijven tegelijk naar hetzelfde doel
  lazen het doel zonder slot), dan houdt de laatst gewijzigde (`modified`) dat nummer; de andere krijgen een vrij
  nummer boven het hoogste van het bestand. Zo wijst elk nummer weer één regel aan. Op het huidige nummer wordt de
  laatst gewijzigde dus de huidige.
- Staat er geen regel op het huidige nummer (de regel schoof met een verplaatsing naar een ander bestand), dan wordt
  de hoogste bestaande regel de huidige; `huidige_versie` en `grootte` gaan mee.
- Heeft het bestand geen regels, dan blijft `huidige_versie_id` leeg (een upload met een basis geeft dan
  `onbekende_basis`).

Elke herstelling staat in de uitvoer van de patch. Opnieuw draaien verandert niets: na de eerste keer staat elk
nummer op één regel en wijst `huidige_versie_id` die regel aan."""

import frappe
from frappe.utils import cint


def execute():
	herstel_dubbele_nummers_en_ontbrekende_regels()
	# Alle andere bestanden: precies één regel op het huidige nummer.
	frappe.db.sql(
		"""update `tabOpslagbestand` b
		join `tabBestandsversie` v on v.bestand = b.name and v.volgnummer = b.huidige_versie
		set b.huidige_versie_id = v.name
		where ifnull(b.huidige_versie_id, '') != v.name"""
	)


def meld(tekst: str) -> None:
	print(f"huidige_versie_id_invullen: {tekst}")


def herstel_dubbele_nummers_en_ontbrekende_regels() -> None:
	dubbel = {
		r[0]
		for r in frappe.db.sql(
			"select bestand from `tabBestandsversie` group by bestand, volgnummer having count(*) > 1"
		)
	}
	zonder = {
		r[0]
		for r in frappe.db.sql(
			"""select b.name from `tabOpslagbestand` b
			where not exists (
				select 1 from `tabBestandsversie` v where v.bestand = b.name and v.volgnummer = b.huidige_versie
			)"""
		)
	}
	for bestand in sorted(dubbel | zonder):
		herstel(bestand)


def herstel(bestand: str) -> None:
	huidige = cint(frappe.db.get_value("Opslagbestand", bestand, "huidige_versie"))
	regels = frappe.db.sql(
		"""select name, volgnummer, grootte, modified from `tabBestandsversie` where bestand = %s
		order by volgnummer, modified, name""",
		bestand,
		as_dict=True,
	)
	if not regels:
		frappe.db.set_value("Opslagbestand", bestand, "huidige_versie_id", None, update_modified=False)
		if huidige:
			meld(f"{bestand}: geen versieregels meer (huidige_versie {huidige}); huidige_versie_id blijft leeg")
		return
	per_nummer: dict[int, list] = {}
	for r in regels:
		per_nummer.setdefault(cint(r.volgnummer), []).append(r)
	if huidige not in per_nummer:
		nieuw = max(per_nummer)
		meld(f"{bestand}: geen regel op huidige versie {huidige}; de hoogste regel ({nieuw}) wordt de huidige")
		huidige = nieuw
	# Per nummer houdt de laatst gewijzigde regel het nummer (de volgorde hierboven: de laatste wint).
	houder = {nummer: rijen[-1] for nummer, rijen in per_nummer.items()}
	vrij = max(per_nummer)
	for nummer, rijen in sorted(per_nummer.items()):
		for r in rijen[:-1]:
			vrij += 1
			frappe.db.set_value("Bestandsversie", r.name, "volgnummer", vrij, update_modified=False)
			meld(
				f"{bestand}: regels {', '.join(x.name for x in rijen)} stonden allemaal op versie {nummer}; "
				f"{r.name} krijgt versie {vrij}, {houder[nummer].name} houdt {nummer}"
			)
	huidig = houder[huidige]
	frappe.db.set_value(
		"Opslagbestand",
		bestand,
		{"huidige_versie": huidige, "huidige_versie_id": huidig.name, "grootte": huidig.grootte},
		update_modified=False,
	)
