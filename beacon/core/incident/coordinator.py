from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from beacon.beacon.doctype.beacon_incident.beacon_incident import OPEN_STATUSES, SCOPE_FIELDS, BeaconIncident
from beacon.beacon.doctype.beacon_notification.beacon_notification import BeaconNotification
from beacon.core.alert.rules import AlertRules
from beacon.core.alert.states import OPEN_ALERT_STATUSES, Severity
from beacon.core.incident.chat_message import ChatMessage
from beacon.core.incident.matcher import IncidentMatcher, OpenIncident, Placement
from beacon.core.resource.registry import ResourceMatch, ResourceRegistry
from beacon.core.transaction import RecordTransaction


class IncidentCoordinator:
	"""Makes every Firing, unsuppressed alert of one region belong to an open incident."""

	def __init__(self, region: str):
		self.region = region
		self.now = now_datetime()

	def reconcile(self) -> None:
		with frappe.db.advisory_lock(f"{frappe.db.cur_db_name}:beacon-invariant:{self.region}", timeout=60):
			self.burn_rules = AlertRules.get_burn_rule_names()
			self.orphans = self._load_orphan_alerts()
			if not self.orphans:
				return

			self.registry = ResourceRegistry(self.region)
			self.burn_resources = self._load_burn_resources()
			self.joined: dict[str, str] = {}
			RecordTransaction.run_each(
				"Beacon Alert",
				self.orphans,
				f"Beacon could not assign an incident in {self.region}",
				self._assign,
			)
			self._announce_joined()

	def _assign(self, name: str) -> None:
		"""Place one alert in its own transaction. The open incidents are read again for each alert, so
		the database is their only owner."""
		alert = self.orphans[name]
		resource = self.registry.get(alert.resource)
		if not resource:
			frappe.log_error(
				f"Beacon alert resource {alert.resource} is not in the registry of {self.region}",
				reference_doctype="Beacon Alert",
				reference_name=name,
			)
			return

		is_burn = alert.rule in self.burn_rules
		placement = IncidentMatcher(self._load_open_incidents(), self.now).find(
			resource, alert.rule, is_burn, self._get_related_resources(alert, is_burn)
		)
		if placement:
			self._join(name, placement, resource)
		else:
			self._open(name, resource)

	def _join(self, name: str, placement: Placement, resource: frappe._dict) -> None:
		"""Lock the incident before the alert. Then check both again: the incident can close and the alert
		can resolve between the queries and the locks. The next run places an alert that is left out."""
		incident = frappe.get_doc("Beacon Incident", placement.incident.name, for_update=True)
		alert = frappe.get_doc("Beacon Alert", name, for_update=True)
		if not (incident.is_open and alert.is_orphan):
			return

		# A worse alert has its own message. The same problem firing again adds nothing to read.
		is_known = frappe.db.exists(
			"Beacon Alert", {"incident": incident.name, "rule": alert.rule, "resource": alert.resource}
		)
		is_worse = Severity.is_higher(alert.severity, incident.severity)
		incident.add_alert(alert, resource if placement.add_scope else None)
		if not (is_worse or is_known):
			self.joined[name] = incident.name

	@staticmethod
	def raise_severity(name: str) -> None:
		"""A firing alert got worse after it joined. Add it again, so its incident takes the higher severity
		and calls on-call. Lock the incident before the alert, as a join does, then check both again."""

		def add_again() -> None:
			incident_name = frappe.db.get_value("Beacon Alert", name, "incident")
			if not incident_name:
				return

			incident = frappe.get_doc("Beacon Incident", incident_name, for_update=True)
			alert = frappe.get_doc("Beacon Alert", name, for_update=True)
			if (
				incident.is_open
				and alert.incident == incident.name
				and alert.status == "Firing"
				and not alert.suppressed
				and Severity.is_higher(alert.severity, incident.severity)
			):
				incident.add_alert(alert)

		RecordTransaction("Beacon Alert", name).run(add_again, "Beacon could not raise an incident severity")

	def _open(self, name: str, resource: frappe._dict) -> None:
		alert = frappe.get_doc("Beacon Alert", name, for_update=True)
		if alert.is_orphan:
			self._take_covered_alerts(BeaconIncident.open_for(alert, resource).name)

	def _take_covered_alerts(self, name: str) -> None:
		"""Move the alerts that the new incident covers out of the open, unacknowledged, Internal
		incidents. An incident with no open alert left closes as Merged."""
		incidents = self._load_open_incidents()
		new = next(item for item in incidents if item.name == name)
		for other in incidents:
			if other.name == name or not other.can_give_alerts:
				continue

			alerts = frappe.get_all(
				"Beacon Alert",
				filters={"incident": other.name, "status": ("in", OPEN_ALERT_STATUSES)},
				fields=["name", "resource"],
			)
			covered = [alert.name for alert in alerts if new.covers(self.registry.get(alert.resource))]
			if not covered:
				continue

			incident = frappe.get_doc("Beacon Incident", other.name, for_update=True)
			if len(covered) == len(alerts):
				incident.close_as_merged(name)
			else:
				frappe.db.set_value(
					"Beacon Alert", {"name": ("in", covered)}, "incident", name, update_modified=False
				)

	def _announce_joined(self) -> None:
		"""One chat message for each incident, so a region outage does not post one message per alert."""
		alerts_of: dict[str, list[str]] = {}
		for alert, incident in self.joined.items():
			alerts_of.setdefault(incident, []).append(alert)

		for incident, alerts in alerts_of.items():
			count = f"{len(alerts)} alert" if len(alerts) == 1 else f"{len(alerts)} alerts"
			rows = [frappe.get_doc("Beacon Alert", alert).describe() for alert in alerts]
			message = f"📥 **{count} added to {ChatMessage.link(incident)}** in {self.region}\n\n"
			message += ChatMessage.alert_table(rows, with_rule=True)
			BeaconNotification.queue("Chat", message, incident)
		frappe.db.commit()  # nosemgrep: the messages only insert new rows

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
			burn_resources[state.service] = resources
		return burn_resources

	def _load_orphan_alerts(self) -> dict[str, frappe._dict]:
		"""The alerts to place, oldest first, and cause alerts before burn alerts."""
		alerts = frappe.get_all(
			"Beacon Alert",
			filters={
				"region": self.region,
				"status": "Firing",
				"suppressed": 0,
				"incident": ("is", "not set"),
			},
			fields=["name", "rule", "resource"],
			order_by="fired_at asc",
		)
		return {
			alert.name: alert for alert in sorted(alerts, key=lambda alert: alert.rule in self.burn_rules)
		}

	def _load_open_incidents(self) -> list[OpenIncident]:
		"""The open incidents with a scope in this region, newest first, with their alerts of this region."""
		incidents = {
			incident.name: OpenIncident(**incident)
			for incident in frappe.get_all(
				"Beacon Incident",
				filters={"type": "Incident", "status": ("in", OPEN_STATUSES)},
				fields=["name", "status", "visibility", "acknowledged_at", "creation"],
				order_by="creation desc",
			)
		}
		names = list(incidents) or [""]
		for scope in frappe.get_all(
			"Beacon Incident Scope",
			filters={"parenttype": "Beacon Incident", "parent": ("in", names), "region": self.region},
			fields=["parent", *SCOPE_FIELDS],
		):
			incidents[scope.parent].matches.append(ResourceMatch.from_row(scope))

		for alert in frappe.get_all(
			"Beacon Alert",
			filters={"incident": ("in", names), "region": self.region},
			fields=["incident", "rule", "resource", "status"],
		):
			is_burn = alert.rule in self.burn_rules
			related = self._get_related_resources(alert, is_burn) if alert.status == "Firing" else None
			incidents[alert.incident].add_alert(alert.rule, is_burn, related)
		return [incident for incident in incidents.values() if incident.matches]
