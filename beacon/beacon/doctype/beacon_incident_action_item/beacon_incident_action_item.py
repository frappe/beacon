from __future__ import annotations

from frappe.model.document import Document


class BeaconIncidentActionItem(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		assignee: DF.Link | None
		is_done: DF.Check
		issue_url: DF.Data | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		title: DF.Data
	# end: auto-generated types

	pass
