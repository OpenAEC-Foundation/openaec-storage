"""Stap 4b (ontwerp samenwerken §5.4): bestaande regels van de wijzigingenlijst krijgen `commitnummer = name`, en de
teller begint boven het hoogste `name`. Een cursor die een client nu bewaart (een `name`), blijft zo geldig."""

import frappe

from openaec_storage.commitnummer import zorg_voor_teller


def execute():
	frappe.db.sql("update `tabOpslagwijziging` set commitnummer = name where commitnummer = 0")
	zorg_voor_teller()
