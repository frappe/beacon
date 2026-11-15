from __future__ import annotations

import copy
from datetime import timedelta
from typing import TYPE_CHECKING

import frappe
from frappe.utils import now_datetime

from beacon.core.alert.rules import AlertRules
from beacon.core.alert.states import OPEN_ALERT_STATUSES, PAGED_SEVERITIES, Severity
from beacon.core.incident.escalation import IncidentEscalation
from beacon.core.incident.lifecycle import OPEN_STATUSES
from beacon.core.incident.matcher import IncidentMatcher, OpenIncident, Placement
from beacon.core.incident.merge import IncidentMerge
from beacon.core.incident.outbox import NotificationOutbox
from beacon.core.resource.registry import ResourceMatch, ResourceRegistry
from beacon.core.transaction import RecordTransaction

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident

# A joined-alerts chat message names this many alerts, then gives the count of the rest.
JOINED_TITLES_SHOWN = 3


class IncidentCoordinator:
	"""Makes every Firing, unsuppressed alert of one region belong to an open incident."""

	def __init__(self, region: str):
		self.region = region
		self.now = now_datetime()

	def reconcile(self) -> None:
		with frappe.db.advisory_lock(f"{frappe.db.cur_db_name}:beacon-invariant:{self.region}", timeout=60):
			self.burn_rules = AlertRules.get_burn_rule_names()
			alerts = self._load_orphan_alerts()
			if not alerts:
				return

			self.registry = ResourceRegistry(self.region)
			self.burn_resources = self._load_burn_resources()
			self.incidents = self._load_open_incidents()
			self.joined: dict[str, list[str]] = {}
			for alert in alerts:
				self._assign_in_transaction(alert)
			self._announce_joined()

	def _assign_in_transaction(self, alert: frappe._dict) -> None:
		"""A retry starts again from the open incidents as they were before this alert."""
		before = copy.deepcopy(self.incidents)

		def assign() -> None:
			self.incidents = copy.deepcopy(before)
			self._assign_incident(alert)

		if not RecordTransaction("Beacon Alert", alert.name).run(
			assign, f"Beacon could not assign an incident in {self.region}"
		):
			self.incidents = before

	def _announce_joined(self) -> None:
		"""One chat message for each incident, so a region outage does not post one message per alert."""
		outbox = NotificationOutbox()
		for name, titles in self.joined.items():
			shown = ", ".join(titles[:JOINED_TITLES_SHOWN])
			rest = len(titles) - JOINED_TITLES_SHOWN
			more = f" and {rest} more" if rest > 0 else ""
			outbox.queue("Chat", f"{len(titles)} alerts joined {name}: {shown}{more}", name)
		frappe.db.commit()  # nosemgrep: the messages only insert new rows

	def _assign_incident(self, alert: frappe._dict) -> None:
		resource = self.registry.get(alert.resource)
		if not resource:
			frappe.log_error(
				f"Beacon alert resource {alert.resource} is not in the registry of {self.region}",
				reference_doctype="Beacon Alert",
				reference_name=alert.name,
			)
			return

		is_burn = alert.rule in self.burn_rules
		group_key = f"{alert.rule}@{self.region}"
		resources = self._get_related_resources(alert, is_burn)
		placement = IncidentMatcher(self.incidents, self.now).find(
			resource, group_key, is_burn, resources
		) or self._reopen_recent(group_key)
		if not placement:
			self._open_incident(alert, resource, group_key, is_burn, resources)
			return

		if placement.add_scope:
			self._add_scope(placement.incident, resource)
		self._add_alert(placement.incident, alert, group_key, is_burn, resources)

	def _get_related_resources(self, alert: frappe._dict, is_burn: bool) -> set[str]:
		return self.burn_resources.get(alert.rule, set()) if is_burn else {alert.resource}

	def _load_burn_resources(self) -> dict[str, set[str]]:
		"""For each burn rule, the bad reporters of its service in this region in the last minute, and their
		parents. An incident with a cause alert on one of them is the incident of that burn."""
		burn_resources = {}
		for state in frappe.get_all(
			"Beacon Service State", filters={"region": self.region}, fields=["service", "bad_reporters"]
		):
			resources = set()
			for name in (state.bad_reporters or "").split():
				if resource := self.registry.get(name):
					resources.update(item.name for item in self.registry.get_ancestors(resource))
			burn_resources[AlertRules.get_burn_rule_name(state.service)] = resources
		return burn_resources

	def _reopen_recent(self, group_key: str) -> Placement | None:
		"""Retrigger an Internal Beacon incident of the same group that resolved within the reopen window."""
		window = frappe.db.get_single_value("Beacon Settings", "reopen_window_minutes") or 30
		names = frappe.get_all(
			"Beacon Incident",
			filters={
				"type": "Incident",
				"status": "Resolved",
				"visibility": "Internal",
				"created_by_beacon": 1,
				"resolved_at": (">", self.now - timedelta(minutes=window)),
				"group_keys": ("like", f"%{group_key}%"),
			},
			pluck="name",
			order_by="resolved_at desc",
			limit=1,
		)
		if not names:
			return None

		incident = frappe.get_doc("Beacon Incident", names[0], for_update=True)
		incident.retrigger()
		item = self._load_open_incident(incident)
		self.incidents.insert(0, item)
		return Placement(item)

	def _add_alert(
		self, item: OpenIncident, alert: frappe._dict, group_key: str, is_burn: bool, resources: set[str]
	) -> None:
		incident = frappe.get_doc("Beacon Incident", item.name, for_update=True)
		frappe.db.set_value("Beacon Alert", alert.name, "incident", item.name, update_modified=False)
		item.add_alert(alert.name, is_burn, resources)
		self.joined.setdefault(item.name, []).append(alert.title)

		is_new_key = group_key not in (incident.group_keys or "").split()
		is_more_severe = Severity.is_higher(alert.severity, incident.severity)
		is_monitoring = incident.status == "Monitoring"
		if not (is_new_key or is_more_severe or is_monitoring):
			return

		incident.add_group_key(group_key)
		if is_more_severe:
			message = f"{incident.name} severity raised from {incident.severity} to {alert.severity} by {alert.title}"
			incident.severity = alert.severity
			NotificationOutbox().announce(incident, f"{message}\n{alert.detail}".rstrip())
		if is_monitoring:
			incident.lifecycle.move_to("Investigating")
		incident.save(ignore_permissions=True)
		if (is_more_severe or is_monitoring) and incident.severity in PAGED_SEVERITIES:
			IncidentEscalation(incident).start(wait=False)

	def _open_incident(
		self, alert: frappe._dict, resource: frappe._dict, group_key: str, is_burn: bool, resources: set[str]
	) -> None:
		rule = frappe.db.get_value(
			"Beacon Alert Rule", alert.rule, ["title", "scope_includes_descendants"], as_dict=True
		)
		incident = frappe.get_doc(
			{
				"doctype": "Beacon Incident",
				"title": f"{rule.title} · {resource.title or resource.resource_id}",
				"type": "Incident",
				"severity": alert.severity,
				"status": "Triggered",
				"visibility": "Internal",
				"created_by_beacon": 1,
				"group_keys": group_key,
				"scope": [
					{
						"region": self.region,
						"resource_type": resource.resource_type,
						"resource_id": resource.resource_id,
						"include_descendants": rule.scope_includes_descendants,
					}
				],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Beacon Alert", alert.name, "incident", incident.name, update_modified=False)

		item = self._load_open_incident(incident)
		item.add_alert(alert.name, is_burn, resources)
		self._take_covered_alerts(item)
		self.incidents.insert(0, item)

		NotificationOutbox().announce(
			incident,
			f"New incident {incident.name} ({incident.severity}): {incident.title}\n{alert.detail}".rstrip(),
			email=True,
		)
		if incident.severity in PAGED_SEVERITIES:
			IncidentEscalation(incident).start(wait=bool(resource.parent_resource))

	def _take_covered_alerts(self, item: OpenIncident) -> None:
		"""Move alerts that the new scope covers out of open, unacknowledged, Internal incidents."""
		emptied = set()
		for other in self.incidents:
			if other.visibility != "Internal" or other.acknowledged_at:
				continue

			alerts = frappe.get_all(
				"Beacon Alert",
				filters={"incident": other.name, "status": ("in", OPEN_ALERT_STATUSES)},
				fields=["name", "resource"],
			)
			covered = [alert.name for alert in alerts if item.covers(self.registry.get(alert.resource))]
			if not covered:
				continue

			other_incident = frappe.get_doc("Beacon Incident", other.name, for_update=True)
			if len(covered) == len(alerts):
				IncidentMerge(other_incident, item.name).close_as_merged()
				emptied.add(other.name)
			else:
				frappe.db.set_value(
					"Beacon Alert", {"name": ("in", covered)}, "incident", item.name, update_modified=False
				)

		self.incidents = [other for other in self.incidents if other.name not in emptied]

	def _add_scope(self, item: OpenIncident, resource: frappe._dict) -> None:
		row = {
			"region": self.region,
			"resource_type": resource.resource_type,
			"resource_id": resource.resource_id,
		}
		incident = frappe.get_doc("Beacon Incident", item.name, for_update=True)
		incident.append("scope", row)
		incident.save(ignore_permissions=True)
		item.matches.append(ResourceMatch.from_row(frappe._dict(row)))

	def _load_orphan_alerts(self) -> list[frappe._dict]:
		alerts = frappe.get_all(
			"Beacon Alert",
			filters={
				"region": self.region,
				"status": "Firing",
				"suppressed": 0,
				"incident": ("is", "not set"),
			},
			fields=["name", "title", "rule", "resource", "severity", "fired_at", "detail"],
			order_by="fired_at asc",
		)
		return sorted(alerts, key=lambda alert: alert.rule in self.burn_rules)

	def _load_open_incidents(self) -> list[OpenIncident]:
		names = frappe.get_all(
			"Beacon Incident Scope",
			filters={"parenttype": "Beacon Incident", "region": self.region},
			pluck="parent",
			distinct=True,
		)
		if not names:
			return []

		open_names = frappe.get_all(
			"Beacon Incident",
			filters={"name": ("in", names), "type": "Incident", "status": ("in", OPEN_STATUSES)},
			pluck="name",
			order_by="creation desc",
		)
		return [self._load_open_incident(frappe.get_doc("Beacon Incident", name)) for name in open_names]

	def _load_open_incident(self, incident: BeaconIncident) -> OpenIncident:
		item = OpenIncident(
			name=incident.name,
			status=incident.status,
			visibility=incident.visibility,
			acknowledged_at=incident.acknowledged_at,
			creation=incident.creation,
			group_keys=set((incident.group_keys or "").split()),
			matches=[ResourceMatch.from_row(row) for row in incident.scope if row.region == self.region],
		)
		for alert in frappe.get_all(
			"Beacon Alert",
			filters={"incident": incident.name, "status": "Firing"},
			fields=["name", "rule", "resource"],
		):
			is_burn = alert.rule in self.burn_rules
			item.add_alert(alert.name, is_burn, self._get_related_resources(alert, is_burn))
		return item
