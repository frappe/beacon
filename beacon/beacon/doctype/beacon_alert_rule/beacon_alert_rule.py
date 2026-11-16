from __future__ import annotations

import time
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document

from beacon.beacon.doctype.beacon_alert.beacon_alert import BeaconAlert
from beacon.core.alert.endpoint_check import ENDPOINT_KINDS, EndpointVariables, get_failure, get_target
from beacon.core.alert.evaluation_schedule import EvaluationSchedule
from beacon.core.alert.rule_evaluator import RuleQuery
from beacon.core.alert.rules import BURN_KIND
from beacon.core.definitions import check_desk_change
from beacon.core.exceptions import ClickHouseError, EndpointVariableError, RuleContractError
from beacon.core.resource.clickhouse import ClickHouseClient
from beacon.core.resource.regions import RegionSelection
from beacon.core.transaction import RecordTransaction, document_action

TEST_SAMPLE_ROWS = 20
CUSTOM_KINDS = ("SQL", "HTTP", "TLS")


class BeaconAlertRule(Document):
	"""A cause rule (SQL), the SLO burn rule of a service, or a rule built into Beacon."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from beacon.beacon.doctype.beacon_region_item.beacon_region_item import BeaconRegionItem

		description: DF.SmallText | None
		enabled: DF.Check
		expected_status: DF.Int
		expected_text: DF.Data | None
		host: DF.Data | None
		interval: DF.Int
		is_custom: DF.Check
		is_removed: DF.Check
		kind: DF.Literal["SQL", "HTTP", "TLS", "SLO Burn", "Built In"]
		minimum_days_left: DF.Int
		pending_for: DF.Int
		query: DF.Code | None
		regions: DF.TableMultiSelect[BeaconRegionItem]
		resolve_after: DF.Int
		resource_type: DF.Data | None
		rule_key: DF.Data
		runbook_url: DF.Data | None
		scope_includes_descendants: DF.Check
		service: DF.Link | None
		silenced_until: DF.Datetime | None
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
			frappe.throw(_("A rule made in Desk is an SQL, HTTP or TLS rule."))
		if self.kind == "SQL" and self.pending_for < self.interval:
			frappe.throw(_("Pending For must be equal to or longer than Interval."))
		if self.kind in ENDPOINT_KINDS:
			self._validate_variables()

	def _validate_variables(self) -> None:
		target = get_target(self)
		unknown = EndpointVariables.get_unknown(target)
		if unknown:
			frappe.throw(_("Unknown variables: {0}.").format(", ".join("{" + name + "}" for name in unknown)))
		if "{central_url}" in target and not frappe.db.get_single_value("Beacon Settings", "central_url"):
			frappe.throw(_("Set the Central URL in Beacon Settings first."))

	def on_update(self) -> None:
		EvaluationSchedule().sync()

	@document_action
	def silence(self, hours: int, reason: str = "") -> None:
		"""Mute every alert of this rule for some hours. 0 hours ends the silence."""
		self.db_set("silenced_until", BeaconAlert.start_silence(self, hours, reason))

	@frappe.whitelist()
	def test(self) -> list[dict]:
		"""Run the query, or check the endpoint, once in each region of the rule. Saves nothing."""
		self.check_permission("write")
		RecordTransaction.start_new()  # release the row lock of the form call before the remote calls
		if self.kind in ENDPOINT_KINDS:
			return [self._test_endpoint(region) for region in self._get_test_regions()]

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

	def _get_test_regions(self) -> list[str]:
		chosen = {row.region for row in self.regions}
		return [region for region in RegionSelection.get_active() if not chosen or region in chosen]

	def _test_endpoint(self, region: str) -> dict[str, Any]:
		try:
			target = EndpointVariables(region).fill(get_target(self))
		except EndpointVariableError as error:
			return {"region": region, "error": str(error)}
		return {
			"region": region,
			"target": target,
			"result": get_failure(self, target) or _("The endpoint answers as expected."),
		}
