from __future__ import annotations

import frappe
from frappe.model.document import Document


class BeaconServiceDay(Document):
	"""Good and valid events and status minutes of one service in one region for one UTC day."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		date: DF.Date | None
		degraded_minutes: DF.Int
		good: DF.Int
		maintenance_minutes: DF.Int
		observed_minutes: DF.Int
		outage_minutes: DF.Int
		partial_outage_minutes: DF.Int
		region: DF.Link | None
		service: DF.Link | None
		valid: DF.Int
	# end: auto-generated types


def on_doctype_update() -> None:
	frappe.db.add_unique("Beacon Service Day", ["service", "region", "date"])
