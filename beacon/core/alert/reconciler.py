from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import frappe
from frappe.utils import now_datetime

from beacon.core.alert.states import OPEN_ALERT_STATUSES, Severity
from beacon.core.alert.suppression import AlertSuppression
from beacon.core.resource.registry import ResourceRegistry

ALERT_FIELDS = (
	"name",
	"status",
	"severity",
	"resource",
	"alert_key",
	"started_at",
	"clean_since",
	"incident",
)
RULE_FIELDS = ("name", "title", "severity", "pending_for", "resolve_after")


@dataclass(frozen=True)
class RuleFailure:
	"""One failing resource that a rule found. Severity overrides the rule's severity."""

	resource: frappe._dict
	alert_key: str = ""
	value: float = 0.0
	detail: str = ""
	severity: str | None = None


@dataclass
class AlertChanges:
	"""What one reconcile changed: alerts that started firing or got more severe, and incidents to check."""

	firing_changes: int = 0
	recovered_incidents: set[str] = field(default_factory=set)


class AlertReconciler:
	"""Turns the failing rows of one rule in one region into alert transitions. Writes only changes."""

	def __init__(self, rule: frappe._dict, registry: ResourceRegistry, now: datetime):
		self.rule = rule
		self.registry = registry
		self.region = registry.region
		self.now = now
		self.inserts: list[dict] = []
		self.updates: dict[str, dict] = {}
		self.changes = AlertChanges()

	@classmethod
	def for_rule(cls, rule: str, registry: ResourceRegistry, now: datetime) -> AlertReconciler:
		return cls(frappe.db.get_value("Beacon Alert Rule", rule, RULE_FIELDS, as_dict=True), registry, now)

	@staticmethod
	def resolve_matching(filters: dict, resolution: str) -> set[str]:
		"""Resolve every open alert that matches the filters. Return their incidents for recovery."""
		alerts = frappe.qb.get_query(
			"Beacon Alert",
			filters={**filters, "status": ("in", OPEN_ALERT_STATUSES)},
			fields=["name", "incident"],
			for_update=True,
		).run(as_dict=True)
		values = AlertReconciler.get_resolution(resolution, now_datetime())
		frappe.db.bulk_update("Beacon Alert", {alert.name: values for alert in alerts})
		return {alert.incident for alert in alerts if alert.incident}

	def reconcile(self, rows: list[RuleFailure]) -> AlertChanges:
		"""Apply one good evaluation: create, keep or resolve each open alert of the rule."""
		open_alerts = {(alert.resource, alert.alert_key): alert for alert in self._load_open_alerts()}
		suppression = AlertSuppression(self.registry, self.now)

		for row in rows:
			alert = open_alerts.pop((row.resource.name, row.alert_key), None)
			if alert:
				self._update_failing(alert, row)
			else:
				self._create(row, suppression)

		for alert in open_alerts.values():
			self._update_clean(alert)

		self._write()
		return self.changes

	def reset_timers(self) -> None:
		"""After an evaluation gap, restart the pending and clean timers: time is no longer continuous."""
		updates = {
			alert.name: {"started_at": self.now} if alert.status == "Pending" else {"clean_since": None}
			for alert in self._load_open_alerts()
		}
		frappe.db.bulk_update("Beacon Alert", updates, update_modified=False)

	def _create(self, row: RuleFailure, suppression: AlertSuppression) -> None:
		is_firing = not self.rule.pending_for
		suppressed_by = suppression.get_suppressed_by(self.rule.name, row.alert_key, row.resource)
		self.inserts.append(
			{
				"name": frappe.generate_hash(length=10),
				"creation": self.now,
				"modified": self.now,
				"owner": "Administrator",
				"modified_by": "Administrator",
				"title": f"{self.rule.title} · {row.resource.resource_id}",
				"status": "Firing" if is_firing else "Pending",
				"severity": row.severity or self.rule.severity,
				"rule": self.rule.name,
				"region": self.region,
				"resource": row.resource.name,
				"resource_type": row.resource.resource_type,
				"alert_key": row.alert_key,
				"first_value": row.value,
				"value": row.value,
				"detail": row.detail[:1000],
				"started_at": self.now,
				"fired_at": self.now if is_firing else None,
				"suppressed": int(bool(suppressed_by)),
				"suppressed_by": suppressed_by,
				# Unique while the alert is open, so a retried evaluation cannot open a second alert.
				"open_key": f"{self.rule.name}|{self.region}|{row.resource.name}|{row.alert_key}"[:140],
			}
		)
		self.changes.firing_changes += int(is_firing)

	@staticmethod
	def get_resolution(resolution: str, now: datetime) -> dict:
		return {"status": "Resolved", "resolution": resolution, "resolved_at": now, "open_key": None}

	def _update_failing(self, alert: frappe._dict, row: RuleFailure) -> None:
		"""A Pending alert fires after pending_for. A Firing alert restarts its clean timer."""
		changes: dict = {}
		if (
			alert.status == "Pending"
			and (self.now - alert.started_at).total_seconds() >= self.rule.pending_for
		):
			changes = {
				"status": "Firing",
				"fired_at": self.now,
				"value": row.value,
				"detail": row.detail[:1000],
			}
		elif alert.status == "Firing" and alert.clean_since:
			changes = {"clean_since": None}

		severity = row.severity or self.rule.severity
		if Severity.is_higher(severity, alert.severity):
			changes["severity"] = severity
		if changes.get("status") == "Firing" or "severity" in changes:
			self.changes.firing_changes += 1
		if changes:
			self.updates[alert.name] = changes

	def _update_clean(self, alert: frappe._dict) -> None:
		"""One clean evaluation clears a Pending alert. A Firing alert recovers after resolve_after clean."""
		changes: dict = {}
		if alert.status == "Pending":
			changes = self.get_resolution("Cleared", self.now)
		elif not alert.clean_since:
			changes = {"clean_since": self.now}
		elif (self.now - alert.clean_since).total_seconds() >= self.rule.resolve_after:
			changes = self.get_resolution("Recovered", self.now)

		if changes.get("resolution") == "Recovered" and alert.incident:
			self.changes.recovered_incidents.add(alert.incident)
		if changes:
			self.updates[alert.name] = changes

	def _write(self) -> None:
		if self.inserts:
			fields = list(self.inserts[0])
			values = [tuple(row.values()) for row in self.inserts]
			frappe.db.bulk_insert("Beacon Alert", fields, values, ignore_duplicates=True)
		frappe.db.bulk_update("Beacon Alert", self.updates)

	def _load_open_alerts(self) -> list[frappe._dict]:
		"""Locked, so the coordinator cannot change an alert between this read and the write."""
		return frappe.qb.get_query(
			"Beacon Alert",
			filters={"rule": self.rule.name, "region": self.region, "status": ("in", OPEN_ALERT_STATUSES)},
			fields=ALERT_FIELDS,
			for_update=True,
		).run(as_dict=True)
