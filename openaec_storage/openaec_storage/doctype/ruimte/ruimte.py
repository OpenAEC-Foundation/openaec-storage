import frappe
from frappe import _
from frappe.model.document import Document


class Ruimte(Document):
	def before_insert(self):
		# Wie een ruimte aanmaakt, wordt er beheerder van.
		gebruiker = frappe.session.user
		if gebruiker not in ("Administrator", "Guest") and not any(lid.gebruiker == gebruiker for lid in self.leden):
			self.append("leden", {"gebruiker": gebruiker, "rol": "beheerder"})

	def raise_no_permission_to(self, perm_type):
		# Wie een nieuwe ruimte mag aanmaken, bepaalt rechten.heeft_recht; hier alleen een begrijpelijke melding.
		if perm_type == "create" and self.is_new():
			frappe.throw(_("Alleen een beheerder van de omgeving kan een ruimte aanmaken."), frappe.PermissionError)
		super().raise_no_permission_to(perm_type)
