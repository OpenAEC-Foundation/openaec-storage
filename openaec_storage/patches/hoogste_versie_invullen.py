"""Versienummers gaan alleen omhoog (api._volgende_versie). Bestaande bestanden krijgen een veilige bovengrens voor
`hoogste_versie`, en een `basisgrens` voor bases van vóór deze patch (api._onzekere_basis).

Het hoogste nummer dat een bestand ooit kreeg, staat nergens. Een te laag nummer is gevaarlijk (een upload kan dan een
oud nummer opnieuw krijgen), een te hoog nummer kan geen kwaad (er wordt een nummer overgeslagen). De bovengrens:
`huidige_versie` plus het aantal regels `verwijderd` voor het bestand in de wijzigingenlijst.

Waarom dat een bovengrens is: alleen `verplaats` met overschrijven verlaagt `huidige_versie`, en altijd met precies 1
(de versies van een bestand zijn 1 tot en met `huidige_versie`: een nieuwe versie komt er bovenop, en verplaatsen haalt
alleen de bovenste weg). Elke zo'n verplaatsing logt een regel `verwijderd` voor de bron, sinds de eerste versie van
`verplaats` (829eb23). Na k verlagingen is het hoogste nummer dus hoogstens `huidige_versie + k`. Een gewone
verwijdering logt ook `verwijderd` zonder te verlagen: dat telt te veel, en is dus veilig. De wijzigingenlijst wordt
nergens opgeruimd.

De grens: de teller voorkomt alleen hergebruik ná deze patch. Een paar (bestand, versie) dat vóór de patch al twee
inhouden had, ziet de patch niet. Dat kon alleen bij een bestand met minstens één regel `verwijderd` (zie hierboven).
Zo'n bestand krijgt als grens het eerste nummer na de patch (`hoogste_versie + 1`): een basis daaronder is onzeker.
Andere bestanden krijgen grens 0; daar blijft een oude basis geldig. Een gewone verwijdering geeft soms onnodig een
grens; dat kost hoogstens één conflictkopie, geen data."""

import frappe


def execute():
	frappe.db.sql("update `tabOpslagbestand` set hoogste_versie = greatest(hoogste_versie, huidige_versie)")
	# Bij een update met een join is de volgorde van de toewijzingen niet vast: beide uitdrukkingen geven daarom
	# hetzelfde, of `hoogste_versie` al is bijgewerkt of niet.
	frappe.db.sql(
		"""update `tabOpslagbestand` b
		join (
			select doel, count(*) as aantal from `tabOpslagwijziging`
			where doel_doctype = 'Opslagbestand' and soort = 'verwijderd'
			group by doel
		) w on w.doel = b.name
		set b.hoogste_versie = greatest(b.hoogste_versie, b.huidige_versie + w.aantal),
			b.basisgrens = greatest(b.basisgrens, greatest(b.hoogste_versie, b.huidige_versie + w.aantal) + 1)"""
	)
