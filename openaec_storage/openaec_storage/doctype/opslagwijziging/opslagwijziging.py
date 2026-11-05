import frappe
from frappe.model.document import Document

from openaec_storage import commitnummer


class Opslagwijziging(Document):
	def validate(self):
		# Het nummer komt alleen van de nummeraar, nooit van wie invoegt of opslaat (ook niet via /api/resource): een
		# te hoog nummer zou de cursor van elke client voorbij alle volgende regels zetten.
		self.commitnummer = 0 if self.is_new() else frappe.db.get_value("Opslagwijziging", self.name, "commitnummer")

	def after_insert(self):
		# Het commitnummer komt vlak voor de commit (zie commitnummer.py).
		commitnummer.meld(self.name)


def on_doctype_update():
	frappe.db.add_index("Opslagwijziging", ["ruimte", "commitnummer"])
