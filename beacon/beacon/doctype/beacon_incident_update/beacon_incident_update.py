from __future__ import annotations

from frappe.model.document import Document


class BeaconIncidentUpdate(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		is_public: DF.Check
		message: DF.Text | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		posted_at: DF.Datetime | None
		posted_by: DF.Link | None
		status: DF.Data
		subscribers_notified_at: DF.Datetime | None
	# end: auto-generated types

	pass
