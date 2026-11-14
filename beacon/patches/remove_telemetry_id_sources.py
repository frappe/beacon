import frappe


def execute() -> None:
	"""Beacon finds a resource by its resource ID only, so a Telemetry IDs source has no use."""
	if frappe.db.has_column("Beacon Resource Source", "kind"):
		frappe.db.delete("Beacon Resource Source", {"kind": "Telemetry IDs"})
