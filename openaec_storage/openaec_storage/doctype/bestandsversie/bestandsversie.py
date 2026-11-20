import frappe
from frappe import _
from frappe.model.document import Document


class Bestandsversie(Document):
	"""Een versieregel verandert nooit en verdwijnt niet via een documentroute (ontwerp samenwerken §5.5, punt 2).

	Haar id is de basis voor vergelijk-en-schrijf (`api._vergelijk_basis`): als `s3_sleutel` kon veranderen, kreeg een
	client bij dezelfde basis andere inhoud, en nam de server een upload vanuit de oude inhoud zonder conflict aan. De
	`has_permission`-haak (`rechten.heeft_recht`) is niet genoeg: Frappe kijkt na een weigering nog naar een expliciete
	deling (`DocShare`), en de Administrator gaat er helemaal langs. Daarom weigert de controller het hier, voor iedereen
	en langs elke route die het document opslaat of wist (`/api/resource`, `frappe.client`, API v2, het formulier).

	De server zelf maakt een regel met `insert(ignore_permissions=True)` en wijzigt er een alleen met
	`frappe.db.set_value` (verplaatsen, het dossier, de patch): dat roept de controller niet aan."""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("Een versieregel kan niet worden gewijzigd"), frappe.PermissionError)
		if not self.flags.ignore_permissions:
			frappe.throw(_("Een versieregel maakt alleen de server"), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(_("Een versieregel kan niet worden verwijderd"), frappe.PermissionError)
