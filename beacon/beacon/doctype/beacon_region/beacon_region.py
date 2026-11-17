from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from beacon.core.alert.evaluation_schedule import EvaluationSchedule
from beacon.core.resource.clickhouse import ClickHouseClient
from beacon.core.resource.logs import LogSearch
from beacon.core.transaction import RecordTransaction, document_action


class BeaconRegion(Document):
	"""One region: its ClickHouse, its telemetry state, and its place on the status page."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		clickhouse_database: DF.Data
		clickhouse_password: DF.Password | None
		clickhouse_url: DF.Data
		clickhouse_user: DF.Data
		region: DF.Data
		reporting_percent: DF.Percent
		show_on_status_page: DF.Check
		sort_order: DF.Int
		status: DF.Literal["Active", "Disabled"]
		telemetry_checked_at: DF.Datetime | None
		telemetry_state: DF.Literal["OK", "Unreachable", "Suspect"]
		telemetry_streak: DF.Int
		title: DF.Data
		wildcard_domain: DF.Data | None
	# end: auto-generated types

	def after_insert(self) -> None:
		"""Region-wide alerts, such as SLO burns, belong to this resource."""
		frappe.get_doc(
			{
				"doctype": "Beacon Resource",
				"region": self.name,
				"resource_id": self.name,
				"resource_type": "Region",
				"title": self.title,
				"status": "Active",
				"is_checked": 1,
				"path": f"/{self.name}",
				"active_since": now_datetime(),
			}
		).insert(ignore_permissions=True)

	def on_update(self) -> None:
		if self.has_value_changed("status"):
			EvaluationSchedule().sync()

	@frappe.whitelist()
	def check_connection(self) -> dict:
		self.check_permission("write")
		RecordTransaction.start_new()  # release the row lock of the form call before the remote calls
		clickhouse = ClickHouseClient(self)
		latency_ms = clickhouse.ping()
		return {"latency_ms": round(latency_ms), "settings": clickhouse.get_limit_settings()}

	@frappe.whitelist(methods=["GET"])
	@frappe.read_only()
	def get_logs(
		self,
		start: str,
		end: str,
		resource: str | None = None,
		levels: str | None = None,
		service: str | None = None,
		search: str | None = None,
		before: str | None = None,
		mode: str = "lines",
	) -> dict:
		"""The logs of every resource in the region, or of one resource when `resource` names it."""
		self.check_permission("read")
		resource_id = None
		if resource:
			row = frappe.db.get_value("Beacon Resource", resource, ["region", "resource_id"], as_dict=True)
			if not row or row.region != self.name:
				frappe.throw(frappe._("{0} is not a resource of {1}.").format(resource, self.name))
			resource_id = row.resource_id

		return LogSearch(self.name, resource_id).get_logs(
			start, end, levels=levels, service=service, search=search, before=before, mode=mode
		)

	@document_action
	def set_status(self, status: str) -> None:
		self.check_permission("write")
		if status not in ("Active", "Disabled"):
			frappe.throw(frappe._("Unknown status {0}.").format(status))
		self.status = status
		self.save()
