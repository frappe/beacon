from __future__ import annotations

from frappe.model.document import Document


class BeaconDefinitionSource(Document):
	"""A GitHub repository whose rules/ and services/ folders Beacon syncs on demand."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		branch: DF.Data
		commit: DF.Data | None
		last_synced_at: DF.Datetime | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		path: DF.Data | None
		repository: DF.Data
		result: DF.SmallText | None
	# end: auto-generated types
