import frappe

from beacon.core.incident.publication import IncidentPublication
from beacon.core.status_page import PUBLISHED_VISIBILITIES


def execute() -> None:
	"""Every published incident opens at /incidents/<public_id>."""
	for name in frappe.get_all(
		"Beacon Incident",
		filters={"visibility": ("in", PUBLISHED_VISIBILITIES), "public_id": ("is", "not set")},
		pluck="name",
	):
		frappe.db.set_value(
			"Beacon Incident", name, "public_id", IncidentPublication.make_public_id(), update_modified=False
		)
