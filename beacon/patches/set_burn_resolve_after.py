import frappe

from beacon.core.alert.rules import BURN_KIND, BURN_RESOLVE_AFTER_SECONDS


def execute():
	"""A saved burn rule keeps the resolve time of its last save. Give every burn rule the current one."""
	frappe.db.set_value(
		"Beacon Alert Rule",
		{"kind": BURN_KIND},
		"resolve_after",
		BURN_RESOLVE_AFTER_SECONDS,
		update_modified=False,
	)
