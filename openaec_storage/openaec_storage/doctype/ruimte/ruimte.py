import frappe
from frappe.model.document import Document


class Ruimte(Document):
	def before_insert(self):
		# Wie een ruimte aanmaakt, wordt er beheerder van.
		gebruiker = frappe.session.user
		if gebruiker not in ("Administrator", "Guest") and not any(lid.gebruiker == gebruiker for lid in self.leden):
			self.append("leden", {"gebruiker": gebruiker, "rol": "beheerder"})
