import frappe
from frappe import _
from frappe.model.document import Document

ARCHIEFVELDEN = ("gearchiveerd", "gearchiveerd_op", "gearchiveerd_door")


def _vergelijkbaar(waarde) -> str:
	# Uit het formulier komt een tijdstip als tekst, uit de database als datetime; tot op de seconde vergelijken.
	return "" if not waarde or str(waarde) == "0" else str(waarde)[:19]


class Ruimte(Document):
	def before_insert(self):
		# Wie een ruimte aanmaakt, wordt er beheerder van.
		gebruiker = frappe.session.user
		if gebruiker not in ("Administrator", "Guest") and not any(lid.gebruiker == gebruiker for lid in self.leden):
			self.append("leden", {"gebruiker": gebruiker, "rol": "beheerder"})
		# Een nieuwe ruimte is nooit gearchiveerd.
		for veld in ARCHIEFVELDEN:
			self.set(veld, None)
		self.gearchiveerd = 0

	def validate(self):
		# Archiveren en terugzetten alleen via ruimtebeheer.archiveer/herstel: die controleren het recht en leggen het
		# vast in de wijzigingenlijst. Een formulier of /api/resource verandert het archief niet.
		if not self.is_new() and not self.flags.via_archief:
			vorige = self.get_doc_before_save()
			if vorige and any(_vergelijkbaar(self.get(veld)) != _vergelijkbaar(vorige.get(veld)) for veld in ARCHIEFVELDEN):
				frappe.throw(_("Archiveren of terugzetten gaat via het beheer"), frappe.PermissionError)

	def raise_no_permission_to(self, perm_type):
		# Wie een nieuwe ruimte mag aanmaken, bepaalt rechten.heeft_recht; hier alleen een begrijpelijke melding.
		if perm_type == "create" and self.is_new():
			frappe.throw(_("Alleen een beheerder van de omgeving kan een ruimte aanmaken."), frappe.PermissionError)
		super().raise_no_permission_to(perm_type)
