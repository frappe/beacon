from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property

import frappe

from beacon.beacon.doctype.beacon_alert.beacon_alert import BeaconAlert
from beacon.core.alert.rules import AlertRules
from beacon.core.alert.suppression import AlertSuppression
from beacon.core.resource.registry import ResourceRegistry

RULE_FIELDS = ("name", "title", "kind", "severity", "pending_for", "resolve_after")


@dataclass(frozen=True)
class RuleFailure:
	"""One failing resource that a rule found. Severity overrides the rule's severity."""

	resource: frappe._dict
	alert_key: str = ""
	value: float = 0.0
	detail: str = ""
	severity: str | None = None


class AlertReconciler:
	"""Compares the failing rows of one rule in one region with its open alerts. Each alert then changes itself."""

	def __init__(self, rule: frappe._dict, registry: ResourceRegistry, now: datetime):
		self.rule = rule
		self.registry = registry
		self.now = now

	@cached_property
	def suppression(self) -> AlertSuppression:
		return AlertSuppression(self.registry, self.now)

	def reconcile(self, failures: list[RuleFailure]) -> None:
		"""Apply one good evaluation: start, keep or resolve each open alert of the rule."""
		open_alerts = {(alert.resource, alert.alert_key or ""): alert for alert in self.get_open_alerts()}

		for failure in failures:
			alert = open_alerts.pop((failure.resource.name, failure.alert_key), None)
			if alert:
				alert.observe_failure(failure, self.rule, self.now)
			else:
				suppressed_by = self.suppression.get_suppressed_by(self.rule.name, failure.resource)
				BeaconAlert.start(self.rule, failure, suppressed_by, self.now)

		for alert in open_alerts.values():
			alert.observe_clean(self.rule, self.now)

	def restart_timers(self) -> None:
		for alert in self.get_open_alerts():
			alert.restart_timers(self.now)

	def get_open_alerts(self) -> list[BeaconAlert]:
		"""Locked, so the coordinator cannot change an alert between this read and the write."""
		return BeaconAlert.get_open(rule=self.rule.name, region=self.registry.region)

	@classmethod
	def for_rule(cls, rule: str, registry: ResourceRegistry, now: datetime) -> AlertReconciler:
		values = frappe.db.get_value("Beacon Alert Rule", rule, RULE_FIELDS, as_dict=True)
		values.title, _ = AlertRules.get_problem_text(values.kind, values.title, None)
		return cls(values, registry, now)
