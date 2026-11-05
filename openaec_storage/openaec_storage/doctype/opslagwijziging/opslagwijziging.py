import frappe
from frappe.model.document import Document

from openaec_storage import commitnummer


class Opslagwijziging(Document):
	def after_insert(self):
		# Het commitnummer komt vlak voor de commit (zie commitnummer.py).
		commitnummer.meld(self.name)


def on_doctype_update():
	frappe.db.add_index("Opslagwijziging", ["ruimte", "commitnummer"])
