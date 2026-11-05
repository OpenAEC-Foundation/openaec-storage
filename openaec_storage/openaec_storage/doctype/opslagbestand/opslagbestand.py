from frappe.model.document import Document
from frappe.utils import cint


class Opslagbestand(Document):
	def validate(self):
		# Versienummers gaan alleen omhoog en komen nooit terug (zie api._volgende_versie): `hoogste_versie` zakt nooit,
		# ook niet bij opslaan via /api/resource, en is nooit lager dan de huidige versie.
		eerder = 0 if self.is_new() else cint(self.get_db_value("hoogste_versie"))
		self.hoogste_versie = max(cint(self.hoogste_versie), cint(self.huidige_versie), eerder)
