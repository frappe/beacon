from __future__ import annotations

import frappe
from frappe.model.document import Document

from beacon.core.jobs import queue_region_evaluation


class BeaconRuleEvaluation(Document):
	"""The schedule and last result of one rule in one region."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		consecutive_errors: DF.Int
		last_duration_ms: DF.Int
		last_error: DF.Code | None
		last_row_count: DF.Int
		last_run_at: DF.Datetime | None
		last_success_at: DF.Datetime | None
		next_run_at: DF.Datetime | None
		region: DF.Link | None
		rule: DF.Link | None
		state: DF.Literal["Healthy", "Erroring", "Broken"]
		unknown_rows: DF.Int
	# end: auto-generated types

	@frappe.whitelist(methods=["POST"])
	def run_now(self) -> None:
		frappe.get_doc("Beacon Alert Rule", self.rule).check_permission("write")
		queue_region_evaluation(self.region, self.name)


def on_doctype_update() -> None:
	frappe.db.add_unique("Beacon Rule Evaluation", ["rule", "region"])
	frappe.db.add_index("Beacon Rule Evaluation", ["next_run_at"])
