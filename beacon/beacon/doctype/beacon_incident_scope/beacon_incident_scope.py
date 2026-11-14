from __future__ import annotations

import frappe
from frappe.model.document import Document


class BeaconIncidentScope(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		include_descendants: DF.Check
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		region: DF.Link
		resource_id: DF.Data | None
		resource_type: DF.Data | None
	# end: auto-generated types

	pass


def on_doctype_update() -> None:
	frappe.db.add_index("Beacon Incident Scope", ["region", "parenttype"])
