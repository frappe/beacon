from __future__ import annotations

from datetime import datetime, timedelta

import frappe

from beacon.beacon.doctype.beacon_alert.beacon_alert import BeaconAlert
from beacon.core.alert.rules import AlertRules
from beacon.core.resource.registry import ResourceMatch, ResourceRegistry

# Alerts in a maintenance scope stay suppressed this long after it ends, so rebooted hosts can come back.
MAINTENANCE_GRACE = timedelta(minutes=10)


class AlertSuppression:
	"""Decides which silence or maintenance mutes an alert in one region."""

	def __init__(self, registry: ResourceRegistry, now: datetime):
		self.registry = registry
		self.region = registry.region
		self.now = now
		self.burn_rules = AlertRules.get_burn_rule_names()
		self.silenced_rules = self._load_silenced_rules(now)
		self.maintenances = self._load_maintenances(now)

	def get_suppressed_by(
		self, rule: str, resource: frappe._dict, silenced_until: datetime | None = None
	) -> str | None:
		"""Return what mutes this alert, or None."""
		if silenced_until and silenced_until > self.now:
			return f"Silenced until {silenced_until:%Y-%m-%d %H:%M} UTC"

		rule_silenced_until = self.silenced_rules.get(rule)
		if rule_silenced_until:
			return f"Rule silenced until {rule_silenced_until:%Y-%m-%d %H:%M} UTC"

		is_burn = rule in self.burn_rules
		for maintenance in self.maintenances:
			if (is_burn and maintenance.mute_slo_burn) or any(
				match.covers(resource) for match in maintenance.matches
			):
				return maintenance.name

		return None

	def refresh_open_alerts(self) -> None:
		"""Recompute the suppression of every open alert in the region."""
		for alert in BeaconAlert.get_open(region=self.region):
			resource = self.registry.get(alert.resource)
			if not resource:
				frappe.log_error(
					f"Beacon alert resource {alert.resource} is not in the registry of {self.region}",
					reference_doctype="Beacon Alert",
					reference_name=alert.name,
				)
				continue

			alert.suppress(self.get_suppressed_by(alert.rule, resource, alert.silenced_until))

	def _load_silenced_rules(self, now: datetime) -> dict[str, datetime]:
		rows = frappe.get_all(
			"Beacon Alert Rule",
			filters={"silenced_until": (">", now)},
			fields=["name", "silenced_until"],
		)
		return {row.name: row.silenced_until for row in rows}

	def _load_maintenances(self, now: datetime) -> list[frappe._dict]:
		maintenances = frappe.get_all(
			"Beacon Incident",
			filters={"type": "Maintenance", "status": ("in", ("In Progress", "Completed"))},
			or_filters=[["status", "=", "In Progress"], ["resolved_at", ">", now - MAINTENANCE_GRACE]],
			fields=["name", "mute_slo_burn"],
		)
		if not maintenances:
			return []

		matches: dict[str, list[ResourceMatch]] = {}
		for row in frappe.get_all(
			"Beacon Incident Scope",
			filters={
				"parenttype": "Beacon Incident",
				"parent": ("in", [maintenance.name for maintenance in maintenances]),
				"region": self.region,
			},
			fields=["parent", "region", "resource_type", "resource_id", "include_descendants"],
		):
			matches.setdefault(row.parent, []).append(ResourceMatch.from_row(row))

		for maintenance in maintenances:
			maintenance.matches = matches.get(maintenance.name, [])
		return [maintenance for maintenance in maintenances if maintenance.matches]
