from __future__ import annotations

import frappe
from frappe.model.document import Document


class BeaconAlert(Document):
	"""One episode of one problem on one resource. Beacon writes it only on transitions."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		alert_key: DF.Data | None
		clean_since: DF.Datetime | None
		detail: DF.SmallText | None
		fired_at: DF.Datetime | None
		first_value: DF.Float
		incident: DF.Link | None
		open_key: DF.Data | None
		region: DF.Link | None
		resolution: DF.Literal["", "Cleared", "Recovered", "Resource Removed", "Disabled"]
		resolved_at: DF.Datetime | None
		resource: DF.Link | None
		resource_type: DF.Data | None
		rule: DF.Link | None
		severity: DF.Literal["Low", "Medium", "High", "Critical"]
		started_at: DF.Datetime | None
		status: DF.Literal["Pending", "Firing", "Resolved"]
		suppressed: DF.Check
		suppressed_by: DF.Data | None
		title: DF.Data | None
		value: DF.Float
	# end: auto-generated types


def on_doctype_update() -> None:
	frappe.db.add_index("Beacon Alert", ["region", "status", "suppressed", "incident"])
	frappe.db.add_index("Beacon Alert", ["rule", "region", "status"])
	frappe.db.add_index("Beacon Alert", ["incident", "status"])
	frappe.db.add_index("Beacon Alert", ["resource", "status"])
	frappe.db.add_index("Beacon Alert", ["status", "resolved_at"])
