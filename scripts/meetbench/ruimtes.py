"""In de bench-container (vanuit sites/): API-sleutel voor Administrator en lege ruimtes voor de meting.
Gebruik: ../env/bin/python ruimtes.py naam1 naam2 ...  -> regels "sleutel <token>" en "<naam> <ruimte-id>"."""

import sys

import frappe

frappe.init(site="sync.localhost", sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
gebruiker = frappe.get_doc("User", "Administrator")
if not gebruiker.api_key:
	gebruiker.api_key = frappe.generate_hash(length=15)
geheim = frappe.generate_hash(length=15)
gebruiker.api_secret = geheim
gebruiker.save(ignore_permissions=True)
print("sleutel", f"token {gebruiker.api_key}:{geheim}")
for naam in sys.argv[1:]:
	bestaand = frappe.db.get_value("Ruimte", {"naam": naam})
	if bestaand:
		frappe.delete_doc("Ruimte", bestaand, force=True, ignore_permissions=True)
		for doctype in ("Opslagwijziging", "Bestandsversie", "Opslagbestand", "Opslagmap"):
			frappe.db.delete(doctype, {"ruimte": bestaand})
	doc = frappe.get_doc(
		{"doctype": "Ruimte", "naam": naam, "soort": "project", "leden": [{"gebruiker": "Administrator", "rol": "beheerder"}]}
	).insert(ignore_permissions=True)
	print(naam, doc.name)
frappe.db.commit()
