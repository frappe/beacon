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
from beacon.core.alert.rules import BURN_KIND, BURN_RESOLVE_AFTER_SECONDS
from beacon.core.definitions import check_desk_change
from beacon.core.exceptions import ClickHouseError, EndpointVariableError, RuleContractError
from beacon.core.resource.clickhouse import ClickHouseClient
from beacon.core.resource.regions import RegionSelection
from beacon.core.service.history import ServiceSample
from beacon.core.service.status import STATUS_THRESHOLDS, ServiceStatus
from beacon.core.transaction import RecordTransaction, document_action

TEST_SAMPLE_ROWS = 20
CUSTOM_KINDS = ("SQL", "HTTP", "TLS")


class BeaconAlertRule(Document):
	"""A cause rule (SQL), a service with its SLO burn rule, or a rule built into Beacon."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from beacon.beacon.doctype.beacon_region_item.beacon_region_item import BeaconRegionItem

		charts: DF.SmallText | None
		description: DF.SmallText | None
		enabled: DF.Check
		expected_status: DF.Int
		expected_text: DF.Data | None
		host: DF.Data | None
		interval: DF.Int
		is_custom: DF.Check
		is_removed: DF.Check
		kind: DF.Literal["SQL", "HTTP", "TLS", "SLO Burn", "Built In"]
		major_outage_below: DF.Percent
		measurement: DF.Literal["Measured", "Declared"]
		minimum_days_left: DF.Int
		page_on_burn: DF.Check
		pending_for: DF.Int
		query: DF.Code | None
		query_region: DF.Link | None
		regions: DF.TableMultiSelect[BeaconRegionItem]
		resolve_after: DF.Int
		resource_type: DF.Data | None
		rule_key: DF.Data
		runbook_url: DF.Data | None
		scope: DF.Literal["Region", "Global"]
		scope_includes_descendants: DF.Check
		severity: DF.Literal["Low", "Medium", "High", "Critical"]
		show_on_status_page: DF.Check
		silenced_until: DF.Datetime | None
		sla_target: DF.Percent
		sli_query: DF.Code | None
		slo_target: DF.Percent
		sort_order: DF.Int
		source: DF.Data | None
		status_page_group: DF.Data | None
		timeout: DF.Int
		title: DF.Data
		url: DF.Data | None
	# end: auto-generated types

	def validate(self) -> None:
		check_desk_change(self)
		if self.kind == BURN_KIND:
			self._validate_service()
		if self.is_custom and self.kind not in CUSTOM_KINDS:
			frappe.throw(_("A rule made in Desk is an SQL, HTTP or TLS rule."))
		if self.kind == "SQL" and self.pending_for < self.interval:
			frappe.throw(_("Pending For must be equal to or longer than Interval."))
		if self.kind in ENDPOINT_KINDS:
			self._validate_variables()

	def _validate_service(self) -> None:
		self.pending_for = 0
		self.resolve_after = BURN_RESOLVE_AFTER_SECONDS
		self.severity = "High"
		self.resource_type = "Region"
		if not 0 < (self.slo_target or 0) < 100:
			frappe.throw(_("The SLO target must be more than 0 and less than 100."))
		if self.measurement == "Measured" and not self.sli_query:
			frappe.throw(_("Add the SLI Query."))
		if self.sla_target and self.sla_target > self.slo_target:
			frappe.throw(_("The SLA target must not be above the SLO target."))
		if not 0 < self.major_outage_below < STATUS_THRESHOLDS[-1][0]:
			frappe.throw(_("Major Outage Below must be more than 0 and less than 95."))

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
		"""Run the query, check the endpoint, or test the SLI once in each region. Saves nothing."""
		self.check_permission("write")
		RecordTransaction.start_new()  # release the row lock of the form call before the remote calls
		if self.kind == BURN_KIND:
			return self._test_sli()
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

	def _test_sli(self) -> list[dict]:
		"""Run the SLI query in each active region. Shows raw good and valid, before registry filters."""
		if self.measurement == "Declared":
			frappe.throw(_("A Declared service has no SLI query."))

		results: list[dict[str, Any]] = []
		for region in RegionSelection.get_active():
			try:
				rows = ClickHouseClient(frappe.get_doc("Beacon Region", region)).query(self.sli_query)
			except ClickHouseError as error:
				results.append({"region": region, "error": str(error)})
				continue

			sample = ServiceSample(
				sum(int(row["good"]) for row in rows), sum(int(row["valid"]) for row in rows)
			)
			results.append(
				{
					"region": region,
					"reporters": len(rows),
					"good": sample.good,
					"valid": sample.valid,
					"ratio": sample.ratio,
					"status": "Unknown"
					if sample.ratio is None
					else ServiceStatus.from_ratio(sample.ratio, self.major_outage_below),
				}
			)
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
