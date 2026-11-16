from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from beacon.beacon.doctype.beacon_alert.beacon_alert import BeaconAlert
from beacon.core.alert.states import OPEN_ALERT_STATUSES, PAGED_SEVERITIES, Severity
from beacon.core.exceptions import IncidentActionError
from beacon.core.incident.chat_message import ChatMessage
from beacon.core.incident.escalation import IncidentEscalation
from beacon.core.incident.outbox import NotificationOutbox
from beacon.core.incident.postmortem import Postmortem
from beacon.core.incident.subscription import IncidentSubscription
from beacon.core.resource.registry import ResourceMatch, ResourceRegistry
from beacon.core.state_machine import StateMachine
from beacon.core.status_page import PUBLISHED_VISIBILITIES, StatusCache
from beacon.core.transaction import RecordTransaction, document_action

REOPEN_DAYS = 7
OPEN_STATUSES = ("Triggered", "Investigating", "Identified", "Monitoring")
SCOPE_FIELDS = ("region", "resource_type", "resource_id", "include_descendants")


class BeaconIncident(Document):
	"""An incident or a maintenance. Its state changes only through the actions of this class."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from beacon.beacon.doctype.beacon_incident_action_item.beacon_incident_action_item import (
			BeaconIncidentActionItem,
		)
		from beacon.beacon.doctype.beacon_incident_scope.beacon_incident_scope import BeaconIncidentScope
		from beacon.beacon.doctype.beacon_incident_service.beacon_incident_service import (
			BeaconIncidentService,
		)
		from beacon.beacon.doctype.beacon_incident_update.beacon_incident_update import BeaconIncidentUpdate

		acknowledged_at: DF.Datetime | None
		action_items: DF.Table[BeaconIncidentActionItem]
		acknowledged_by: DF.Link | None
		affected_services: DF.Table[BeaconIncidentService]
		alerts_recovered_at: DF.Datetime | None
		created_by_beacon: DF.Check
		detected_by: DF.Literal["Alert", "Engineer", "Customer"]
		escalation: DF.Literal["None", "Waiting", "Calling", "Done"]
		excluded_from_slo: DF.Check
		merged_into: DF.Link | None
		mute_slo_burn: DF.Check
		next_page_at: DF.Datetime | None
		page_index: DF.Int
		page_round: DF.Int
		postmortem_required: DF.Check
		public_id: DF.Data | None
		public_rca: DF.TextEditor | None
		rca: DF.TextEditor | None
		resolved_at: DF.Datetime | None
		scheduled_end: DF.Datetime | None
		scheduled_start: DF.Datetime | None
		scope: DF.Table[BeaconIncidentScope]
		severity: DF.Literal["Low", "Medium", "High", "Critical"]
		status: DF.Literal[
			"Triggered",
			"Investigating",
			"Identified",
			"Monitoring",
			"Resolved",
			"Merged",
			"Scheduled",
			"In Progress",
			"Completed",
			"Cancelled",
		]
		title: DF.Data
		type: DF.Literal["Incident", "Maintenance"]
		updates: DF.Table[BeaconIncidentUpdate]
		visibility: DF.Literal["Internal", "Unlisted", "Public"]
	# end: auto-generated types

	STATUS = StateMachine(
		"status",
		{
			"Triggered": ("Investigating", "Identified", "Monitoring", "Resolved", "Merged"),
			"Investigating": ("Identified", "Monitoring", "Resolved", "Merged"),
			"Identified": ("Investigating", "Monitoring", "Resolved", "Merged"),
			"Monitoring": ("Investigating", "Identified", "Resolved", "Merged"),
			"Resolved": ("Investigating",),
			"Scheduled": ("In Progress", "Cancelled"),
			"In Progress": ("Completed",),
		},
	)

	@property
	def is_open(self) -> bool:
		return self.status in OPEN_STATUSES

	@property
	def has_open_alerts(self) -> bool:
		return bool(
			frappe.db.exists("Beacon Alert", {"incident": self.name, "status": ("in", OPEN_ALERT_STATUSES)})
		)

	def before_insert(self) -> None:
		self.status = "Scheduled" if self.type == "Maintenance" else self.status or "Investigating"
		self.visibility = self.visibility or "Internal"
		if self.created_by_beacon:
			self.detected_by = "Alert"

	def validate(self) -> None:
		if not self.is_new() and self.has_value_changed("type"):
			frappe.throw(_("You cannot change the type of an incident after you create it."))
		if not self.is_new() and self.has_value_changed("created_by_beacon"):
			frappe.throw(_("You cannot change Created by Beacon."))
		if (
			self.type == "Maintenance"
			and self.scheduled_start
			and self.scheduled_end
			and get_datetime(self.scheduled_start) >= get_datetime(self.scheduled_end)
		):
			frappe.throw(_("Scheduled start must be before scheduled end."))
		if self.status == "Resolved" and self.has_value_changed("status"):
			Postmortem(self).prepare_on_resolve()

	def on_update(self) -> None:
		if self.visibility != "Internal":
			StatusCache.clear(self.public_id)
			IncidentSubscription.queue_new_updates(self)

	@document_action
	def acknowledge(self) -> None:
		self.check_permission("write")
		self.require_open()
		if self.acknowledged_by:
			return

		self.acknowledged_by = frappe.session.user
		self.acknowledged_at = now_datetime()
		self.STATUS.move(self, "Investigating" if self.status == "Triggered" else self.status)
		IncidentEscalation(self).stop()
		frappe.db.set_value(
			"Beacon Notification",
			{
				"incident": self.name,
				"channel": "Call",
				"status": ("in", ("Queued", "Sending", "Sent", "Answered")),
			},
			"status",
			"Acknowledged",
		)
		self.save()

	@document_action
	def post_update(self, message: str, status: str | None = None, is_public: bool = False) -> None:
		self.check_permission("write")
		if self.type == "Maintenance":
			self.require_status("Scheduled", "In Progress")
		elif status == "Resolved":
			self.resolve(message, is_public)
			return
		else:
			self.require_open()
			if status and status not in OPEN_STATUSES:
				frappe.throw(_("Choose Investigating, Identified or Monitoring."), IncidentActionError)
			self.STATUS.move(self, status or self.status)

		self.add_update(message, is_public)
		self.save()

	@document_action
	def resolve(self, message: str, is_public: bool = False, silence_hours: int = 0) -> None:
		"""Close the incident. Unsuppressed firing alerts block it unless they are silenced for silence_hours."""
		self.check_permission("write")
		self.require_open()
		firing = frappe.get_all(
			"Beacon Alert",
			filters={"incident": self.name, "status": "Firing", "suppressed": 0},
			pluck="name",
		)
		if firing and not silence_hours:
			frappe.throw(
				_(
					"{0} alerts still fire. Fix the problem first, or enter hours to silence these alerts."
				).format(len(firing)),
				IncidentActionError,
			)

		for name in firing:
			frappe.get_doc("Beacon Alert", name, for_update=True).silence(
				silence_hours, f"Resolved {self.name} while it still fired."
			)
		self.add_update(message, is_public, status="Resolved")
		self.STATUS.move(self, "Resolved")
		self.close()
		self.save()
		NotificationOutbox().announce(
			self, f"✅ **Resolved** {ChatMessage.link(self.name)}: {message}", email=True
		)

	@document_action
	def reopen(self) -> None:
		self.check_permission("write")
		if self.status != "Resolved" or get_datetime(self.resolved_at) < now_datetime() - timedelta(
			days=REOPEN_DAYS
		):
			frappe.throw(
				_("You can reopen only a Resolved incident, and only for {0} days after it closes.").format(
					REOPEN_DAYS
				),
				IncidentActionError,
			)

		self.STATUS.move(self, "Investigating")
		self.resolved_at = None
		# A person reopened it with no open alert, so the recovery sweep must not close it again.
		self.alerts_recovered_at = self.alerts_recovered_at or now_datetime()
		self.add_update("Reopened.", is_public=False)
		self.save()

	@document_action
	def page_on_call(self) -> None:
		self.check_permission("write")
		self.require_open()
		escalation = IncidentEscalation(self)
		if escalation.is_active:
			frappe.throw(
				_("Beacon already notifies the on-call team about this incident."), IncidentActionError
			)
		escalation.start(wait=False)
		self.save()

	@document_action
	def merge_into(self, target: str) -> None:
		"""Close this incident as Merged. Its scope and its open alerts go to the target."""
		self.check_permission("write")
		self.require_open()
		incident = frappe.get_doc("Beacon Incident", target, for_update=True)
		if incident.name == self.name or not incident.is_open or incident.type != "Incident":
			frappe.throw(_("Choose a different incident that is still open."), IncidentActionError)
		if self.visibility != "Internal" and incident.visibility == "Internal":
			frappe.throw(
				_("Customers can see this incident, so it cannot merge into an Internal incident."),
				IncidentActionError,
			)

		for row in self.scope:
			incident.append("scope", {field: row.get(field) for field in SCOPE_FIELDS})
		incident.save()
		self.close_as_merged(incident.name)

	@document_action
	def publish(self, visibility: str, message: str, affected_services: list[dict] | None = None) -> None:
		"""Show the incident to customers. It gets a link with a random public ID, so nobody can guess the
		link of an Unlisted incident or count the incidents."""
		self.check_permission("write")
		if self.visibility != "Internal" or visibility not in PUBLISHED_VISIBILITIES:
			frappe.throw(_("Choose Public or Unlisted."), IncidentActionError)

		self.visibility = visibility
		if affected_services is not None:
			self.set("affected_services", affected_services)
		self.public_id = frappe.generate_hash(length=32)
		if (
			self.type == "Maintenance"
			and visibility == "Public"
			and now_datetime() < get_datetime(self.scheduled_start)
		):
			self.excluded_from_slo = 1
		self.add_update(message, is_public=True)
		self.save()

	@document_action
	def change_visibility(self, visibility: str) -> None:
		"""The link stays the same, so a link that support already sent keeps working."""
		self.check_permission("write")
		if self.visibility == "Internal":
			frappe.throw(
				_("This incident is Internal. Use Publish to show it to customers."), IncidentActionError
			)
		if visibility not in PUBLISHED_VISIBILITIES or visibility == self.visibility:
			frappe.throw(_("Choose Public or Unlisted."), IncidentActionError)

		self.visibility = visibility
		self.save()

	@document_action
	def regenerate_public_link(self) -> None:
		self.check_permission("write")
		self.require_published()
		old_id = self.public_id
		self.public_id = frappe.generate_hash(length=32)
		self.save()
		StatusCache.clear(old_id)

	@frappe.whitelist()
	@frappe.read_only()
	def get_public_link(self) -> str:
		self.check_permission("read")
		self.require_published()
		return frappe.get_cached_doc("Beacon Settings").get_status_page_url(f"/incidents/{self.public_id}")

	@frappe.whitelist()
	@frappe.read_only()
	def get_affected_resources(self) -> list[list[str]]:
		"""The resources in the scope and under the incident's alerts, read from the registry when asked, as CSV rows."""
		self.check_permission("read")
		alerts = frappe.get_all(
			"Beacon Alert", filters={"incident": self.name}, fields=["region", "resource", "rule"]
		)
		with_descendants = set(
			frappe.get_all(
				"Beacon Alert Rule",
				filters={
					"name": ("in", [alert.rule for alert in alerts] or [""]),
					"scope_includes_descendants": 1,
				},
				pluck="name",
			)
		)
		regions = {*(scope.region for scope in self.scope), *(alert.region for alert in alerts)}
		registries = {region: ResourceRegistry(region) for region in sorted(regions)}

		matches = [ResourceMatch.from_row(scope) for scope in self.scope]
		for alert in alerts:
			resource = registries[alert.region].get(alert.resource)
			if resource:
				matches.append(
					ResourceMatch(
						alert.region,
						resource.resource_type,
						resource.resource_id,
						alert.rule in with_descendants,
					)
				)

		columns = ("region", "resource_id", "resource_type", "title", "status", "path")
		rows = [["Region", "Resource ID", "Type", "Title", "Status", "Path"]]
		for registry in registries.values():
			for resource in sorted(registry.by_name.values(), key=lambda row: row.path or ""):
				if any(match.covers(resource) for match in matches):
					rows.append([resource.get(column) for column in columns])
		return rows

	@document_action
	def start_maintenance(self) -> None:
		self.check_permission("write")
		self.STATUS.move(self, "In Progress")
		self.scheduled_start = min(get_datetime(self.scheduled_start), now_datetime())
		self.save()

	@document_action
	def extend_maintenance(self, scheduled_end: str) -> None:
		self.check_permission("write")
		self.require_status("In Progress")
		if get_datetime(scheduled_end) <= max(now_datetime(), get_datetime(self.scheduled_end)):
			frappe.throw(_("Choose an end time later than the current end."), IncidentActionError)
		self.scheduled_end = scheduled_end
		self.save()

	@document_action
	def complete_maintenance(self) -> None:
		self.check_permission("write")
		self.STATUS.move(self, "Completed")
		self.resolved_at = now_datetime()
		self.save()

	@document_action
	def cancel_maintenance(self) -> None:
		self.check_permission("write")
		self.STATUS.move(self, "Cancelled")
		self.save()

	def add_alert(self, alert: BeaconAlert, scope_resource: frappe._dict | None = None) -> None:
		"""Beacon's action: take a firing alert, and cover scope_resource when it is given. A worse alert
		raises the severity. A worse alert, or any alert while the incident is in Monitoring, notifies
		on-call again."""
		alert.join(self.name)
		is_worse = Severity.is_higher(alert.severity, self.severity)
		was_monitoring = self.status == "Monitoring"
		if not (is_worse or was_monitoring or scope_resource or self.alerts_recovered_at):
			return

		self.alerts_recovered_at = None
		if scope_resource:
			self.append(
				"scope",
				{
					"region": scope_resource.region,
					"resource_type": scope_resource.resource_type,
					"resource_id": scope_resource.resource_id,
				},
			)
		if is_worse:
			row = alert.describe()
			NotificationOutbox().announce(
				self,
				f"{ChatMessage.header(alert.severity, self.name, alert.region)}\n\n"
				f"**Severity raised from {self.severity} to {alert.severity}** by {row.rule_title}\n\n"
				f"{ChatMessage.alert_table([row])}",
			)
			self.severity = alert.severity
		self.STATUS.move(self, "Investigating" if was_monitoring else self.status)
		if (is_worse or was_monitoring) and self.severity in PAGED_SEVERITIES:
			IncidentEscalation(self).start(wait=False)
		self.save(ignore_permissions=True)

	def close_as_merged(self, target: str) -> None:
		"""Move the open alerts to the target and close this incident."""
		frappe.db.set_value(
			"Beacon Alert",
			{"incident": self.name, "status": ("in", OPEN_ALERT_STATUSES)},
			"incident",
			target,
			update_modified=False,
		)
		self.merged_into = target
		self.STATUS.move(self, "Merged")
		self.close()
		self.save(ignore_permissions=True)
		NotificationOutbox().announce(
			self, f"🔀 {ChatMessage.link(self.name)} **merged into** {ChatMessage.link(target)}"
		)

	def record_recovery(self) -> None:
		"""Beacon's action when no alert of the incident is open any more. Beacon resolves an Internal
		incident that it made. A person resolves every other incident, so Beacon posts to chat one time."""
		if not self.is_open or self.alerts_recovered_at or self.has_open_alerts:
			return

		self.alerts_recovered_at = now_datetime()
		if self.visibility == "Internal" and self.created_by_beacon:
			self.resolve("All alerts recovered.")
			return

		self.save(ignore_permissions=True)
		NotificationOutbox().announce(
			self,
			f"⏸️ **No open alerts remain** on {ChatMessage.link(self.name)}. Check the service, then resolve it.",
		)

	def close(self) -> None:
		"""Stop the notifications and free the open alerts for a new incident. Does not save."""
		self.resolved_at = now_datetime()
		IncidentEscalation(self).stop()
		frappe.db.set_value(
			"Beacon Alert",
			{"incident": self.name, "status": ("in", OPEN_ALERT_STATUSES)},
			"incident",
			None,
			update_modified=False,
		)

	def add_update(self, message: str, is_public: bool, status: str | None = None) -> None:
		"""An update is shown to customers only when the incident is published."""
		self.append(
			"updates",
			{
				"posted_at": now_datetime(),
				"status": status or self.status,
				"message": message,
				"is_public": int(is_public and self.visibility != "Internal"),
				"posted_by": frappe.session.user,
			},
		)

	def get_final_incident(self) -> str:
		"""Follow merged_into to the incident that now holds the work. A merge loop stops where it repeats."""
		name, seen = self.name, set()
		while name not in seen:
			seen.add(name)
			merged_into = frappe.db.get_value("Beacon Incident", name, "merged_into")
			if not merged_into:
				return name
			name = merged_into
		return name

	def require_open(self) -> None:
		if not self.is_open:
			frappe.throw(
				_("You cannot do this because the incident is {0}.").format(self.status), IncidentActionError
			)

	def require_status(self, *statuses: str) -> None:
		if self.status not in statuses:
			frappe.throw(
				_("You can only do this when it is {0}.").format(_(" or ").join(statuses)),
				IncidentActionError,
			)

	def require_published(self) -> None:
		if self.visibility not in PUBLISHED_VISIBILITIES or not self.public_id:
			frappe.throw(_("Only a Public or Unlisted incident has a link."), IncidentActionError)

	@staticmethod
	def open_for(alert: BeaconAlert, resource: frappe._dict) -> BeaconIncident:
		"""Beacon's action: open an Internal incident for a firing alert that no open incident takes. The
		on-call notification waits when the resource has a parent, so an incident of the parent can take
		the alert first."""
		rule = frappe.get_cached_value(
			"Beacon Alert Rule", alert.rule, ["title", "scope_includes_descendants"], as_dict=True
		)
		incident = frappe.get_doc(
			{
				"doctype": "Beacon Incident",
				"title": alert.title,
				"type": "Incident",
				"severity": alert.severity,
				"status": "Triggered",
				"visibility": "Internal",
				"created_by_beacon": 1,
				"scope": [
					{
						"region": alert.region,
						"resource_type": resource.resource_type,
						"resource_id": resource.resource_id,
						"include_descendants": rule.scope_includes_descendants,
					}
				],
			}
		).insert(ignore_permissions=True)
		alert.join(incident.name)

		NotificationOutbox().announce(
			incident,
			f"{ChatMessage.header(incident.severity, incident.name, alert.region)}\n\n"
			f"**{rule.title}**\n\n{ChatMessage.alert_table([alert.describe()])}",
			email=True,
		)
		if incident.severity in PAGED_SEVERITIES:
			IncidentEscalation(incident).start(wait=bool(resource.parent_resource))
			incident.save(ignore_permissions=True)
		return incident

	@staticmethod
	def close_recovered() -> None:
		"""Scheduled every minute: find each open incident whose alerts all recovered. A full sweep, so a
		missed run costs time and never leaves an incident open."""
		names = frappe.get_all(
			"Beacon Incident",
			filters={
				"type": "Incident",
				"status": ("in", OPEN_STATUSES),
				"alerts_recovered_at": ("is", "not set"),
			},
			pluck="name",
		)
		alerts = frappe.get_all(
			"Beacon Alert",
			filters={"incident": ("in", names or [""])},
			fields=["incident", "status"],
			distinct=True,
		)
		with_open_alerts = {alert.incident for alert in alerts if alert.status in OPEN_ALERT_STATUSES}
		recovered = {alert.incident for alert in alerts} - with_open_alerts

		RecordTransaction.run_each(
			"Beacon Incident",
			sorted(recovered),
			"Beacon could not close a recovered incident",
			lambda name: frappe.get_doc("Beacon Incident", name, for_update=True).record_recovery(),
		)

	@staticmethod
	def advance_maintenances() -> None:
		"""Scheduled every minute: start and complete each maintenance at its scheduled time."""
		BeaconIncident._run_due_maintenances("Scheduled", "scheduled_start", "start_maintenance")
		BeaconIncident._run_due_maintenances("In Progress", "scheduled_end", "complete_maintenance")

	@staticmethod
	def _run_due_maintenances(status: str, time_field: str, action: str) -> None:
		names = frappe.get_all(
			"Beacon Incident",
			filters={"type": "Maintenance", "status": status, time_field: ("<=", now_datetime())},
			pluck="name",
		)

		def run(name: str) -> None:
			maintenance = frappe.get_doc("Beacon Incident", name, for_update=True)
			if maintenance.status == status:
				getattr(maintenance, action)()

		RecordTransaction.run_each(
			"Beacon Incident", names, f"Beacon could not {action.replace('_', ' ')}", run
		)


def on_doctype_update() -> None:
	frappe.db.add_index("Beacon Incident", ["status", "type"])
	frappe.db.add_index("Beacon Incident", ["visibility", "status"])
