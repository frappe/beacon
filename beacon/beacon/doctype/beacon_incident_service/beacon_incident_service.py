from __future__ import annotations

from frappe.model.document import Document


class BeaconIncidentService(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		impact: DF.Literal["Degraded", "Partial Outage", "Major Outage", "Maintenance"]
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		region: DF.Link | None
		service: DF.Link
	# end: auto-generated types

	pass
