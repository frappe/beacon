from __future__ import annotations

import frappe
from frappe.model.document import Document


class BeaconServiceMinute(Document):
	"""Good and valid events of one service in one region for one minute. Kept 90 days."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		bad_reporters: DF.Int
		good: DF.Int
		minute: DF.Datetime | None
		region: DF.Link | None
		reporters: DF.Int
		service: DF.Link | None
		status: DF.Literal[
			"Operational", "Degraded", "Partial Outage", "Major Outage", "Maintenance", "Unknown"
		]
		valid: DF.Int
	# end: auto-generated types


def on_doctype_update() -> None:
	frappe.db.add_unique("Beacon Service Minute", ["service", "region", "minute"])
