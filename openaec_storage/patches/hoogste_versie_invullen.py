"""Versienummers gaan alleen omhoog (api._volgende_versie): bestaande bestanden krijgen een veilige bovengrens voor
`hoogste_versie`.

Het hoogste nummer dat een bestand ooit kreeg, staat nergens. Een te laag nummer is gevaarlijk (een upload kan dan een
oud nummer opnieuw krijgen), een te hoog nummer kan geen kwaad (er wordt een nummer overgeslagen). De bovengrens:
`huidige_versie` plus het aantal regels `verwijderd` voor het bestand in de wijzigingenlijst.

Waarom dat een bovengrens is: alleen `verplaats` met overschrijven verlaagt `huidige_versie`, en altijd met precies 1
(de versies van een bestand zijn 1 tot en met `huidige_versie`: een nieuwe versie komt er bovenop, en verplaatsen haalt
alleen de bovenste weg). Elke zo'n verplaatsing logt een regel `verwijderd` voor de bron, sinds de eerste versie van
`verplaats` (829eb23). Na k verlagingen is het hoogste nummer dus hoogstens `huidige_versie + k`. Een gewone
verwijdering logt ook `verwijderd` zonder te verlagen: dat telt te veel, en is dus veilig. De wijzigingenlijst wordt
nergens opgeruimd."""

import frappe


def execute():
	frappe.db.sql(
		"""update `tabOpslagbestand` b
		left join (
			select doel, count(*) as aantal from `tabOpslagwijziging`
			where doel_doctype = 'Opslagbestand' and soort = 'verwijderd'
			group by doel
		) w on w.doel = b.name
		set b.hoogste_versie = greatest(b.hoogste_versie, b.huidige_versie + coalesce(w.aantal, 0))"""
	)
