"""Het commitnummer van de wijzigingenlijst (ontwerp samenwerken §5.4, K16).

Frappe geeft een Opslagwijziging haar `name` bij het invoegen (een sequence), dus in de volgorde van invoegen en niet
in die van committen. Voorbeeld: transactie A krijgt 41 en blijft open, B krijgt 42 en commit. Een client leest 42 en
bewaart cursor 42; daarna commit A, en `name > 42` laat 41 nooit meer zien.

Daarom krijgt een regel bij het invoegen commitnummer 0, en deelt de lezer de nummers uit: vóór elke lezing van de
lijst nummert `veeg()` de regels die al vastgelegd zijn en nog nummer 0 hebben. Dat gebeurt onder het slot op één
tellerregel, in een eigen korte transactie die commit vóór de lezing. Een regel krijgt zo pas een nummer als hij al
vastgelegd is, en altijd een hoger nummer dan elk nummer dat eerder is uitgedeeld. Een client heeft alleen nummers
gezien die al uitgedeeld waren. Een regel die later zichtbaar wordt, krijgt dus een hoger nummer dan de cursor van
elke client. Langs welke weg de regel is vastgelegd, maakt niet uit: een gewone commit, een impliciete commit door
DDL, een rauwe SQL-commit of een achtergrondtaak. De schrijver doet niets en neemt het tellerslot nooit.

Een teruggedraaide regel krijgt geen nummer, dus een rollback laat geen gat. Een gat binnen één ruimte is gewoon een
regel van een andere ruimte."""

import frappe
from frappe.utils import cint

TELLER = "openaec_storage.commitnummer"  # de tellerregel in tabSeries
POGINGEN = 5  # zo vaak begint de veger opnieuw na fout 1020 of 1213


def veeg() -> None:
	"""Nummer de vastgelegde regels met nummer 0, vóór een lezing van de wijzigingenlijst (de stappen uit §5.4).

	Na `veeg()` is de lopende transactie gecommit als er iets te nummeren was. De aanroeper leest daarna gewoon, met
	een nieuwe momentopname. Een GET-verzoek draait aan het eind terug (`frappe/app.py`, `sync_database`); daarom
	commit de veger zelf, vóór de lezing."""
	# 1. Een gewone lezing in de lopende transactie. Geen regel met nummer 0: klaar, geen slot.
	if not frappe.db.sql("select 1 from `tabOpslagwijziging` where commitnummer = 0 limit 1"):
		return
	# 2. De lopende transactie moet leeg zijn, want de veger commit haar. Een lezer schrijft zelf niets; staat er toch
	# schrijfwerk in, of mag er niet gecommit worden (in een doc-haak), dan is dat een programmeerfout.
	if frappe.db.transaction_writes or frappe.db._disable_transaction_control:
		raise RuntimeError("De veger van de wijzigingenlijst moet de lopende transactie committen, maar dat kan hier niet")
	for poging in range(1, POGINGEN + 1):
		try:
			frappe.db.commit()
			# 3. Het slot op de teller. Dit is de eerste lezing van de nieuwe transactie, dus wie hier wacht, krijgt
			# daarna de laatste stand (zonder fout 1020).
			teller = cint(frappe.db.sql("select current from `tabSeries` where name = %s for update", TELLER)[0][0])
			# 4. Een gewone lezing; de momentopname begint pas hier, na het slot. Alleen vastgelegde regels: een regel
			# van een open transactie blijft 0 en krijgt in een latere ronde een hoger nummer.
			namen = frappe.db.sql_list("select name from `tabOpslagwijziging` where commitnummer = 0 order by name")
			# 5. De teller eerst, dan de regels in volgorde van `name`. Zo valt een regel nooit boven de teller.
			if namen:
				frappe.db.sql("update `tabSeries` set current = %s where name = %s", (teller + len(namen), TELLER))
			for nummer, name in enumerate(namen, teller + 1):
				frappe.db.sql(
					"update `tabOpslagwijziging` set commitnummer = %s where name = %s and commitnummer = 0",
					(nummer, name),
				)
			# 6. Commit vóór de lezing: anders gaan de nummers na een GET-verzoek verloren, en geeft de volgende veger
			# ze aan andere regels.
			frappe.db.commit()
			return
		except Exception as fout:
			# Een halve veging komt nooit vast: de transactie bevat alleen werk van de veger.
			frappe.db.rollback()
			if not isinstance(fout, frappe.QueryDeadlockError) or poging == POGINGEN:
				raise


def zorg_voor_teller() -> None:
	"""De tellerregel, en nooit lager dan het hoogste `name`. Een uitgedeeld nummer is nooit groter dan het hoogste
	`name` (de teller begint daar, en loopt alleen op voor vastgelegde regels), dus zo valt een nieuw nummer nooit onder
	een bestaand nummer of onder een cursor die een client al bewaart."""
	hoogste = cint(frappe.db.sql("select coalesce(max(name), 0) from `tabOpslagwijziging`")[0][0])
	frappe.db.sql(
		"""insert into `tabSeries` (name, current) values (%(teller)s, %(hoogste)s)
		on duplicate key update current = greatest(current, %(hoogste)s)""",
		{"teller": TELLER, "hoogste": hoogste},
	)
