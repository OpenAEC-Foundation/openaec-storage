import frappe
from frappe import _
from frappe.model.document import Document


class Opslagmap(Document):
	def validate(self):
		self.naam = (self.naam or "").strip()
		if not self.naam or "/" in self.naam or "\\" in self.naam or self.naam in (".", ".."):
			frappe.throw(_("Ongeldige mapnaam"))
		if self.bovenliggende:
			if self.bovenliggende == self.name:
				frappe.throw(_("Een map kan niet in zichzelf staan"))
			if frappe.db.get_value("Opslagmap", self.bovenliggende, "ruimte") != self.ruimte:
				frappe.throw(_("De bovenliggende map hoort bij een andere ruimte"))
		dubbel = frappe.db.sql(
			"""select name from `tabOpslagmap`
			where ruimte = %s and ifnull(bovenliggende, '') = %s and lower(naam) = lower(%s)
			and verwijderd_op is null and name != %s""",
			(self.ruimte, self.bovenliggende or "", self.naam, self.name or ""),
		)
		if dubbel:
			frappe.throw(_("Er staat al een map met deze naam op deze plek"))
