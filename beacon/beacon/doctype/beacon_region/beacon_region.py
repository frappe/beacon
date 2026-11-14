from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from beacon.core.alert.evaluation_schedule import EvaluationSchedule
from beacon.core.resource.clickhouse import ClickHouseClient
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

	@document_action
	def set_status(self, status: str) -> None:
		self.check_permission("write")
		if status not in ("Active", "Disabled"):
			frappe.throw(frappe._("Unknown status {0}.").format(status))
		self.status = status
		self.save()
