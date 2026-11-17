from __future__ import annotations

from frappe.model.document import Document


class BeaconChart(Document):
	"""A read-only chart definition that a definition source syncs."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		chart_key: DF.Data
		is_key_chart: DF.Check
		query: DF.Code
		requires_metric: DF.Data | None
		resource_type: DF.Data | None
		section: DF.Data | None
		sort_order: DF.Int
		source: DF.Data | None
		style: DF.Literal["Line", "Bar"]
		title: DF.Data
		unit: DF.Data | None
	# end: auto-generated types

	pass
