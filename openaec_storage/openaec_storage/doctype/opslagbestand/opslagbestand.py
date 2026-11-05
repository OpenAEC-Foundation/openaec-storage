import frappe
from frappe.model.document import Document
from frappe.utils import cint


class Opslagbestand(Document):
	def validate(self):
		# Versienummers gaan alleen omhoog en komen nooit terug (zie api._volgende_versie): `hoogste_versie` zakt nooit,
		# ook niet bij opslaan via /api/resource, en is nooit lager dan de huidige versie. De `basisgrens` zet alleen de
		# patch; bij opslaan blijft hij staan.
		if self.is_new():
			hoogste, self.basisgrens = 0, 0
		else:
			hoogste, self.basisgrens = frappe.db.get_value(self.doctype, self.name, ["hoogste_versie", "basisgrens"])
		self.hoogste_versie = max(cint(self.hoogste_versie), cint(self.huidige_versie), cint(hoogste))
