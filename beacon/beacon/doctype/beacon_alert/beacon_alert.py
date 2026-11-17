from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, now_datetime

from beacon.core.alert.rules import AlertRules
from beacon.core.alert.states import OPEN_ALERT_STATUSES, Severity
from beacon.core.state_machine import StateMachine
from beacon.core.transaction import document_action

if TYPE_CHECKING:
	from beacon.core.alert.reconciler import RuleFailure

DETAIL_LENGTH = 1000
SILENCE_LIMIT_HOURS = 30 * 24


class BeaconAlert(Document):
	"""One problem on one resource, from its start to its end. Beacon writes it only when it changes."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		alert_key: DF.Data | None
		clean_since: DF.Datetime | None
		detail: DF.SmallText | None
		fired_at: DF.Datetime | None
		first_value: DF.Float
		incident: DF.Link | None
		open_key: DF.Data | None
		region: DF.Link | None
		resolution: DF.Literal["", "Cleared", "Recovered", "Resource Removed", "Disabled"]
		resolved_at: DF.Datetime | None
		resource: DF.Link | None
		resource_type: DF.Data | None
		rule: DF.Link | None
		severity: DF.Literal["Low", "Medium", "High", "Critical"]
		silenced_until: DF.Datetime | None
		started_at: DF.Datetime | None
		status: DF.Literal["Pending", "Firing", "Resolved"]
		suppressed: DF.Check
		suppressed_by: DF.Data | None
		title: DF.Data | None
		value: DF.Float
	# end: auto-generated types

	STATUS = StateMachine("status", {"Pending": ("Firing", "Resolved"), "Firing": ("Resolved",)})

	@property
	def is_orphan(self) -> bool:
		"""True when the alert must join an incident."""
		return self.status == "Firing" and not self.suppressed and not self.incident

	def observe_failure(self, failure: RuleFailure, rule: frappe._dict, now: datetime) -> None:
		"""The rule still returns the alert. A Pending alert takes the severity of its row and fires after
		pending_for. A Firing alert only goes up in severity, and then keeps the row that made it worse."""
		severity = failure.severity or rule.severity
		if self.status == "Pending":
			if (now - self.started_at).total_seconds() >= rule.pending_for:
				self.fire(failure, severity, now)
			else:
				self._save_changes(severity=severity)
		elif Severity.is_higher(severity, self.severity):
			self._save_changes(
				severity=severity,
				value=failure.value,
				detail=failure.detail[:DETAIL_LENGTH],
				clean_since=None,
			)
		else:
			self._save_changes(clean_since=None)

	def observe_clean(self, rule: frappe._dict, now: datetime) -> None:
		"""The rule no longer returns the alert. A Pending alert clears at once. A Firing alert recovers
		after resolve_after without a row."""
		if self.status == "Pending":
			self.resolve("Cleared", now)
		elif not self.clean_since:
			self._save_changes(clean_since=now)
		elif (now - self.clean_since).total_seconds() >= rule.resolve_after:
			self.resolve("Recovered", now)

	def fire(self, failure: RuleFailure, severity: str, now: datetime) -> None:
		self.STATUS.move(self, "Firing")
		self.update(
			{
				"severity": severity,
				"fired_at": now,
				"value": failure.value,
				"detail": failure.detail[:DETAIL_LENGTH],
			}
		)
		self.save(ignore_permissions=True)

	def resolve(self, resolution: str, now: datetime) -> None:
		"""Close the alert. The open key becomes free, so the same problem can start a new alert."""
		self.STATUS.move(self, "Resolved")
		self.update({"resolution": resolution, "resolved_at": now, "open_key": None})
		self.save(ignore_permissions=True)

	def restart_timers(self, now: datetime) -> None:
		"""After a gap in the evaluations, time is no longer continuous."""
		if self.status == "Pending":
			self._save_changes(started_at=now)
		else:
			self._save_changes(clean_since=None)

	def suppress(self, suppressed_by: str | None) -> None:
		"""Record what mutes the alert, or None when nothing does."""
		self._save_changes(suppressed=int(bool(suppressed_by)), suppressed_by=suppressed_by)

	@document_action
	def silence(self, hours: int, reason: str = "") -> None:
		"""Mute this open alert for some hours: it joins no incident. 0 hours ends the silence."""
		if self.status not in OPEN_ALERT_STATUSES:
			frappe.throw(_("This alert is resolved."))

		self.silenced_until = BeaconAlert.start_silence(self, hours, reason)
		if self.silenced_until:
			self.suppressed = 1
			self.suppressed_by = f"Silenced until {self.silenced_until:%Y-%m-%d %H:%M} UTC"
		self.save(ignore_permissions=True)

	@staticmethod
	def start_silence(document: Document, hours: int, reason: str) -> datetime | None:
		"""Check the silence of an alert or a rule, note the reason, and return its end. A rule silence reaches
		its alerts in the next region run."""
		frappe.has_permission("Beacon Alert", "write", throw=True)
		hours = int(hours or 0)
		if not 0 <= hours <= SILENCE_LIMIT_HOURS:
			frappe.throw(_("Silence for 0 to {0} hours.").format(SILENCE_LIMIT_HOURS))
		if hours and not (reason or "").strip():
			frappe.throw(_("Enter why you silence it."))

		document.add_comment(
			"Comment", _("Silenced for {0} h: {1}").format(hours, reason) if hours else _("Silence ended.")
		)
		return add_to_date(now_datetime(), hours=hours) if hours else None

	def join(self, incident: str) -> None:
		self.incident = incident
		self.save(ignore_permissions=True)

	def describe(self) -> frappe._dict:
		"""The alert as a row of a chat table."""
		resource = frappe.db.get_value(
			"Beacon Resource", self.resource, ["title", "resource_id"], as_dict=True
		)
		rule = frappe.get_cached_value(
			"Beacon Alert Rule", self.rule, ["kind", "title", "description"], as_dict=True
		)
		title, _ = AlertRules.get_problem_text(rule.kind, rule.title, rule.description)
		return frappe._dict(
			rule_title=title,
			resource_title=resource.title or resource.resource_id,
			value=self.value,
			detail=self.detail,
		)

	@staticmethod
	def start(rule: frappe._dict, failure: RuleFailure, suppressed_by: str | None, now: datetime) -> None:
		"""Open an alert for a new failing row. A rule with no pending time fires at once."""
		resource = failure.resource
		is_firing = not rule.pending_for
		preposition = "in" if resource.resource_type == "Region" else "on"
		frappe.get_doc(
			{
				"doctype": "Beacon Alert",
				"title": f"{rule.title} {preposition} {resource.title or resource.resource_id}",
				"status": "Firing" if is_firing else "Pending",
				"severity": failure.severity or rule.severity,
				"rule": rule.name,
				"region": resource.region,
				"resource": resource.name,
				"resource_type": resource.resource_type,
				"alert_key": failure.alert_key,
				"first_value": failure.value,
				"value": failure.value,
				"detail": failure.detail[:DETAIL_LENGTH],
				"started_at": now,
				"fired_at": now if is_firing else None,
				"suppressed": int(bool(suppressed_by)),
				"suppressed_by": suppressed_by,
				# Unique while the alert is open, so a repeated evaluation cannot open a second alert.
				"open_key": f"{rule.name}|{resource.region}|{resource.name}|{failure.alert_key}"[:140],
			}
		).insert(ignore_permissions=True, ignore_if_duplicate=True)

	@staticmethod
	def get_open(**filters) -> list[BeaconAlert]:
		"""Lock and load the open alerts that match, in one query."""
		rows = frappe.qb.get_query(
			"Beacon Alert",
			filters={**filters, "status": ("in", OPEN_ALERT_STATUSES)},
			fields=["*"],
			for_update=True,
		).run(as_dict=True)
		return [frappe.get_doc({"doctype": "Beacon Alert", **row}) for row in rows]

	@staticmethod
	def resolve_open(resolution: str, **filters) -> None:
		now = now_datetime()
		for alert in BeaconAlert.get_open(**filters):
			alert.resolve(resolution, now)

	def _save_changes(self, **changes) -> None:
		"""Write only when a value changes, so an alert that stays the same causes no write."""
		changes = {
			field: value for field, value in changes.items() if (self.get(field) or None) != (value or None)
		}
		if changes:
			self.update(changes)
			self.save(ignore_permissions=True)


def on_doctype_update() -> None:
	frappe.db.add_index("Beacon Alert", ["region", "status", "suppressed", "incident"])
	frappe.db.add_index("Beacon Alert", ["rule", "region", "status"])
	frappe.db.add_index("Beacon Alert", ["incident", "status"])
	frappe.db.add_index("Beacon Alert", ["resource", "status"])
	frappe.db.add_index("Beacon Alert", ["status", "resolved_at"])
