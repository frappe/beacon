import frappe


def execute() -> None:
	"""A silence is now a field of an alert or a rule, not a record of its own."""
	frappe.delete_doc("DocType", "Beacon Silence", force=True, ignore_missing=True)
	frappe.db.sql_ddl("drop table if exists `tabBeacon Silence`")
