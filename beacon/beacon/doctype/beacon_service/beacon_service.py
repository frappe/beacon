from __future__ import annotations

from typing import Any

import frappe
from frappe.model.document import Document

from beacon.core.alert.rules import BURN_KIND, AlertRules
from beacon.core.definitions import check_desk_change
from beacon.core.exceptions import ClickHouseError
from beacon.core.resource.clickhouse import ClickHouseClient
from beacon.core.resource.regions import RegionSelection
from beacon.core.service.history import ServiceSample
from beacon.core.service.status import STATUS_THRESHOLDS, ServiceStatus
from beacon.core.transaction import RecordTransaction

BURN_RULE_RESOLVE_AFTER_SECONDS = 300


class BeaconService(Document):
	"""One line on the status page, with its SLI query and SLO."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from beacon.beacon.doctype.beacon_region_item.beacon_region_item import BeaconRegionItem

		description: DF.SmallText | None
		enabled: DF.Check
		is_removed: DF.Check
		major_outage_below: DF.Percent
		measurement: DF.Literal["Measured", "Declared"]
		page_on_burn: DF.Check
		query_region: DF.Link | None
		regions: DF.TableMultiSelect[BeaconRegionItem]
		scope: DF.Literal["Region", "Global"]
		service_key: DF.Data
		show_on_status_page: DF.Check
		sla_target: DF.Percent
		sli_query: DF.Code
		slo_target: DF.Percent
		source: DF.Data | None
		sort_order: DF.Int
		status_page_group: DF.Data | None
		title: DF.Data
	# end: auto-generated types

	def validate(self) -> None:
		check_desk_change(self)
		if self.sla_target and self.sla_target > self.slo_target:
			frappe.throw(frappe._("The SLA target must not be above the SLO target."))
		if not 0 < self.major_outage_below < STATUS_THRESHOLDS[-1][0]:
			frappe.throw(frappe._("Major Outage Below must be more than 0 and less than 95."))

	def on_update(self) -> None:
		"""Every service has one SLO burn rule. The tick computes it from Service Minute."""
		name = AlertRules.get_burn_rule_name(self.name)
		rule = (
			frappe.get_doc("Beacon Alert Rule", name)
			if frappe.db.exists("Beacon Alert Rule", name)
			else frappe.new_doc("Beacon Alert Rule")
		)
		rule.update(
			{
				"rule_key": name,
				"title": f"{self.title} SLO burn",
				"kind": BURN_KIND,
				"enabled": self.enabled,
				"service": self.name,
				"severity": "High",
				"resource_type": "Region",
				"pending_for": 0,
				"resolve_after": BURN_RULE_RESOLVE_AFTER_SECONDS,
			}
		)
		rule.flags.loading_definitions = True
		rule.save(ignore_permissions=True)

	@frappe.whitelist()
	def test(self) -> list[dict]:
		"""Run the SLI query in each active region. Shows raw good and valid, before registry filters."""
		self.check_permission("write")
		RecordTransaction.start_new()  # release the row lock of the form call before the remote calls
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
