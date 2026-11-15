from __future__ import annotations

import frappe
from frappe.model.document import Document


class BeaconServiceState(Document):
	"""The status, budget and burn rates of one service in one region. Beacon writes it every minute."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		bad_reporters: DF.SmallText | None
		budget_left: DF.Percent
		burn_rates: DF.Data | None
		candidate_since: DF.Datetime | None
		candidate_status: DF.Literal[
			"Operational", "Degraded", "Partial Outage", "Major Outage", "Maintenance", "Unknown"
		]
		checked_at: DF.Datetime | None
		last_counted_minute: DF.Datetime | None
		last_ratio: DF.Percent
		region: DF.Link | None
		service: DF.Link | None
		status: DF.Literal[
			"Operational", "Degraded", "Partial Outage", "Major Outage", "Maintenance", "Unknown"
		]
	# end: auto-generated types


def on_doctype_update() -> None:
	frappe.db.add_unique("Beacon Service State", ["service", "region"])
