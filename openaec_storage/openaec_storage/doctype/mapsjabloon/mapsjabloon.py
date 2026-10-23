import frappe
from frappe import _
from frappe.model.document import Document

from openaec_storage.mapsjablonen import extensies, splits_pad


class Mapsjabloon(Document):
	def validate(self):
		self.naam = (self.naam or "").strip()
		gezien = set()
		for rij in self.mappen:
			delen = splits_pad(rij.pad)
			rij.pad = "/".join(delen)
			sleutel = rij.pad.lower()
			if sleutel in gezien:
				frappe.throw(_("De map {0} staat twee keer in het sjabloon").format(rij.pad))
			gezien.add(sleutel)
			rij.weergavenaam = (rij.weergavenaam or "").strip() or delen[-1]
			rij.extensies = ", ".join(extensies(rij.extensies))

	def on_update(self):
		# Eén standaardsjabloon per soort: wie dit sjabloon standaard maakt, haalt het vinkje bij de andere weg.
		if self.standaard:
			for ander in frappe.get_all(
				"Mapsjabloon",
				filters={"soort": self.soort, "standaard": 1, "name": ("!=", self.name)},
				pluck="name",
				ignore_permissions=True,
			):
				frappe.db.set_value("Mapsjabloon", ander, "standaard", 0)
