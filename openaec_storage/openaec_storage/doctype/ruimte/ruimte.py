import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

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
		# De regels per map van een nieuwe ruimte komen uit het mapsjabloon (after_insert), als er een is.
		self.set("mapregels", [])

	def after_insert(self):
		if self.mapsjabloon:
			from openaec_storage.mapsjablonen import leg_mappen_aan

			leg_mappen_aan(self)

	def validate(self):
		from openaec_storage.rechten import mag_ruimte_maken

		if flt(self.quotum_gb) < 0:
			frappe.throw(_("Het quotum van een ruimte kan niet negatief zijn"))
		self.controleer_mapregels()
		vorige = None if self.is_new() else self.get_doc_before_save()
		if not vorige:
			return
		# Archiveren en terugzetten alleen via ruimtebeheer.archiveer/herstel: die controleren het recht en leggen het
		# vast in de wijzigingenlijst. Een formulier of /api/resource verandert het archief niet.
		if not self.flags.via_archief and any(
			_vergelijkbaar(self.get(veld)) != _vergelijkbaar(vorige.get(veld)) for veld in ARCHIEFVELDEN
		):
			frappe.throw(_("Archiveren of terugzetten gaat via het beheer"), frappe.PermissionError)
		# Het eigen quotum is een afspraak met de omgeving: een beheerder van de ruimte verhoogt het niet zelf.
		if flt(self.quotum_gb) != flt(vorige.quotum_gb) and not mag_ruimte_maken():
			frappe.throw(
				_("Alleen een beheerder van de omgeving kan het quotum van een ruimte wijzigen"), frappe.PermissionError
			)

	def controleer_mapregels(self):
		from openaec_storage.mapsjablonen import extensies

		for regel in self.get("mapregels") or []:
			if regel.map and frappe.db.get_value("Opslagmap", regel.map, "ruimte") != self.name:
				frappe.throw(_("Een regel verwijst naar een map uit een andere ruimte"))
			regel.extensies = ", ".join(extensies(regel.extensies))

	def raise_no_permission_to(self, perm_type):
		# Wie een nieuwe ruimte mag aanmaken, bepaalt rechten.heeft_recht; hier alleen een begrijpelijke melding.
		if perm_type == "create" and self.is_new():
			frappe.throw(_("Alleen een beheerder van de omgeving kan een ruimte aanmaken."), frappe.PermissionError)
		super().raise_no_permission_to(perm_type)
