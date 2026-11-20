import frappe
from frappe.model.document import Document


class Opslagbestand(Document):
	def validate(self):
		# Welke versie de huidige is, zet alleen de server (api._nieuwe_versie en verplaats, de uploadlink, met
		# ignore_permissions of db.set_value). Wie het bestand als document opslaat (bijv. via /api/resource), kan
		# `huidige_versie` en `huidige_versie_id` niet veranderen: anders kon een oude basis weer de huidige worden.
		if self.flags.ignore_permissions:
			return
		if self.is_new():
			self.huidige_versie_id = None
		else:
			self.huidige_versie, self.huidige_versie_id = frappe.db.get_value(
				self.doctype, self.name, ["huidige_versie", "huidige_versie_id"]
			)
