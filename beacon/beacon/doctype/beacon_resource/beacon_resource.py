from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from beacon.core.resource.charts import ResourceCharts, get_time_window
from beacon.core.resource.registry import ResourceRegistry
from beacon.core.resource.tree import ResourceTree
from beacon.core.transaction import document_action


class BeaconResource(Document):
	"""One region, host, VM or service node. The registry sync owns the fields from the source."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		active_since: DF.Datetime | None
		disabled_by: DF.Link | None
		disabled_reason: DF.SmallText | None
		is_checked: DF.Check
		missing_fetches: DF.Int
		monitoring_disabled: DF.Check
		parent_resource: DF.Link | None
		path: DF.Data | None
		region: DF.Link | None
		removal_held: DF.Check
		resource_id: DF.Data | None
		resource_type: DF.Data | None
		row_hash: DF.Data | None
		source: DF.Link | None
		status: DF.Literal["Pending", "Active", "Suspended", "Terminated"]
		terminated_at: DF.Datetime | None
		title: DF.Data | None
		url: DF.Data | None
	# end: auto-generated types

	def autoname(self) -> None:
		self.name = ResourceRegistry.make_name(self.region, self.resource_id)

	@document_action
	def disable_monitoring(self, reason: str) -> None:
		self.check_permission("write")
		self.monitoring_disabled = 1
		self.disabled_reason = reason
		self.disabled_by = frappe.session.user
		self.save()
		ResourceTree(self.region).rebuild()

	@document_action
	def enable_monitoring(self) -> None:
		"""Checks start again after the new-resource grace."""
		self.check_permission("write")
		self.monitoring_disabled = 0
		self.disabled_reason = None
		self.disabled_by = None
		self.active_since = now_datetime()
		self.save()
		ResourceTree(self.region).rebuild()

	@frappe.whitelist(methods=["GET"])
	@frappe.read_only()
	def get_charts(self, start: str, end: str) -> dict:
		self.check_permission("read")
		start_time, end_time, _step = get_time_window(start, end)
		charts = ResourceCharts(self)
		return {
			"charts": charts.get_definitions(start_time, end_time),
			"alerts": charts.get_alerts(start_time, end_time),
		}

	@frappe.whitelist(methods=["GET"])
	@frappe.read_only()
	def get_chart(self, chart: str, start: str, end: str) -> dict:
		self.check_permission("read")
		return ResourceCharts(self).get_chart(chart, start, end)


def on_doctype_update() -> None:
	frappe.db.add_index("Beacon Resource", ["region", "path"])
	frappe.db.add_index("Beacon Resource", ["parent_resource"])
	frappe.db.add_index("Beacon Resource", ["source", "status"])
