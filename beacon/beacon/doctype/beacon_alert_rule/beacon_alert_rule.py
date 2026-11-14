from __future__ import annotations

import time
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document

from beacon.core.alert.evaluation_schedule import EvaluationSchedule
from beacon.core.alert.http_check import HttpChecks
from beacon.core.alert.rule_evaluator import RuleQuery
from beacon.core.alert.rules import BURN_KIND
from beacon.core.definitions import check_desk_change
from beacon.core.exceptions import ClickHouseError, RuleContractError
from beacon.core.resource.clickhouse import ClickHouseClient
from beacon.core.resource.regions import RegionSelection
from beacon.core.transaction import RecordTransaction

TEST_SAMPLE_ROWS = 20
CUSTOM_KINDS = ("SQL", "HTTP")


class BeaconAlertRule(Document):
	"""A cause rule (SQL), the SLO burn rule of a service, or a rule built into Beacon."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from beacon.beacon.doctype.beacon_region_item.beacon_region_item import BeaconRegionItem

		enabled: DF.Check
		expected_status: DF.Int
		expected_text: DF.Data | None
		interval: DF.Int
		is_custom: DF.Check
		is_removed: DF.Check
		kind: DF.Literal["SQL", "HTTP", "SLO Burn", "Built In"]
		pending_for: DF.Int
		query: DF.Code | None
		regions: DF.TableMultiSelect[BeaconRegionItem]
		resolve_after: DF.Int
		resource_type: DF.Data | None
		rule_key: DF.Data
		runbook_url: DF.Data | None
		scope_includes_descendants: DF.Check
		service: DF.Link | None
		source: DF.Data | None
		severity: DF.Literal["Low", "Medium", "High", "Critical"]
		timeout: DF.Int
		title: DF.Data
		url: DF.Data | None
	# end: auto-generated types

	def validate(self) -> None:
		check_desk_change(self)
		if (
			self.kind == BURN_KIND
			and self.has_value_changed("enabled")
			and not self.flags.loading_definitions
		):
			frappe.throw(_("Turn the service on or off instead."))
		if self.is_custom and self.kind not in CUSTOM_KINDS:
			frappe.throw(_("A rule made in Desk is an SQL or HTTP rule."))
		if self.kind == "SQL" and self.pending_for < self.interval:
			frappe.throw(_("Pending For must be equal to or longer than Interval."))
		if self.kind == "HTTP" and len(self.regions) != 1:
			frappe.throw(
				_("An HTTP rule needs exactly one region. Its alerts and incidents belong to that region.")
			)

	def on_update(self) -> None:
		EvaluationSchedule().sync()

	@frappe.whitelist()
	def test(self) -> list[dict]:
		"""Run the query in every active region, or call the URL once. Saves nothing."""
		self.check_permission("write")
		RecordTransaction.start_new()  # release the row lock of the form call before the remote calls
		if self.kind == "HTTP":
			return [
				{
					"url": self.url,
					"result": HttpChecks.get_failure(self) or _("The URL answers as expected."),
				}
			]

		results: list[dict[str, Any]] = []
		for region in RegionSelection.get_active():
			started = time.monotonic()
			try:
				rows = RuleQuery(ClickHouseClient(frappe.get_doc("Beacon Region", region)), self.query).run()
				results.append({"region": region, "rows": len(rows), "sample": rows[:TEST_SAMPLE_ROWS]})
			except (ClickHouseError, RuleContractError) as error:
				results.append({"region": region, "error": str(error)})
			results[-1]["duration_ms"] = round((time.monotonic() - started) * 1000)
		return results
