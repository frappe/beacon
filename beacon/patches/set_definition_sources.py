import frappe

# Each app keeps the rules and services that read its telemetry in its own repository.
SOURCES = ("frappe/atlas", "frappe/cargo", "frappe/central", "frappe/pilot")

# Rules that came from app files keep this owner. It is not a configured source, so each app's source takes its rules over.
FILE_OWNER = "frappe/beacon"


def execute() -> None:
	"""Rules and services that came from app files now come from the definition source of each app."""
	for doctype, condition in (("Beacon Alert Rule", {"kind": "SQL"}), ("Beacon Service", {})):
		if not frappe.db.has_column(doctype, "is_from_file"):
			continue
		table = frappe.qb.DocType(doctype)
		query = frappe.qb.update(table).set(table.source, FILE_OWNER).where(table.is_from_file == 1)
		for field, value in condition.items():
			query = query.where(table[field] == value)
		query.run()

	if frappe.db.count("Beacon Definition Source"):
		return
	for index, repository in enumerate(SOURCES, start=1):
		frappe.get_doc(
			{
				"doctype": "Beacon Definition Source",
				"parent": "Beacon Settings",
				"parenttype": "Beacon Settings",
				"parentfield": "definition_sources",
				"idx": index,
				"repository": repository,
				"branch": "develop",
				"path": "beacon",
			}
		).db_insert()
