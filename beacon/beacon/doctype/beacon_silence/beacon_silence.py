from __future__ import annotations

from datetime import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, get_datetime, now_datetime

from beacon.core.alert.states import OPEN_ALERT_STATUSES
from beacon.core.resource.registry import ResourceMatch
from beacon.core.transaction import document_action

MATCH_FIELDS = ("region", "resource_type", "resource_id", "rule", "alert_key")


class BeaconSilence(Document):
	"""Mutes paging for matching alerts until it expires. It starts when it is saved."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		alert_key: DF.Data | None
		expires_at: DF.Datetime
		include_descendants: DF.Check
		reason: DF.Data
		region: DF.Link | None
		resource_id: DF.Data | None
		resource_type: DF.Data | None
		revoked_at: DF.Datetime | None
		revoked_by: DF.Link | None
		rule: DF.Link | None
	# end: auto-generated types

	def validate(self) -> None:
		if not any(self.get(field) for field in MATCH_FIELDS):
			frappe.throw(_("Enter a value in one or more match fields."))
		if self.has_value_changed("expires_at"):
			self._check_expiry(self.expires_at)

	@frappe.whitelist()
	@frappe.read_only()
	def preview_matches(self) -> dict:
		self.check_permission("read")
		if not any(self.get(field) for field in MATCH_FIELDS):
			return {"resources": [], "alerts": [], "resource_count": 0, "alert_count": 0}
		match = ResourceMatch.from_row(self)
		resources = [
			row
			for row in frappe.get_list(
				"Beacon Resource",
				filters={"region": self.region} if self.region else {},
				fields=["name", "title", "region", "resource_id", "resource_type", "path"],
				limit_page_length=0,
			)
			if match.covers(row)
		]
		filters = {
			"resource": ("in", [row.name for row in resources]),
			"status": ("in", OPEN_ALERT_STATUSES),
		}
		for field in ("rule", "alert_key"):
			if self.get(field):
				filters[field] = self.get(field)
		alerts = (
			frappe.get_list(
				"Beacon Alert",
				filters=filters,
				fields=["name", "title", "status", "resource", "rule", "alert_key"],
				limit_page_length=0,
			)
			if resources
			else []
		)
		return {
			"resources": resources[:20],
			"alerts": alerts[:20],
			"resource_count": len(resources),
			"alert_count": len(alerts),
		}

	@document_action
	def revoke(self) -> None:
		self.check_permission("write")
		self.revoked_at = now_datetime()
		self.revoked_by = frappe.session.user
		self.save()

	@document_action
	def extend_expiry(self, expires_at: str) -> None:
		self.check_permission("write")
		self.expires_at = expires_at
		self.save()

	@staticmethod
	def create_for_alert(alert: frappe._dict, hours: int, reason: str) -> None:
		"""Silence one alert exactly: its rule, alert key and resource."""
		frappe.get_doc(
			{
				"doctype": "Beacon Silence",
				"region": alert.region,
				"resource_id": alert.resource.split(":", 1)[1],
				"rule": alert.rule,
				"alert_key": alert.alert_key,
				"expires_at": add_to_date(now_datetime(), hours=hours),
				"reason": reason,
			}
		).insert()

	def _check_expiry(self, expires_at: str | datetime) -> None:
		now = now_datetime()
		if not now < get_datetime(expires_at) <= add_to_date(now, days=30):
			frappe.throw(_("Expires At must be in the next 30 days."))
