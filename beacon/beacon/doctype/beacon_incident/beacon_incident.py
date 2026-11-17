from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Coalesce
from frappe.utils import get_datetime, now_datetime

from beacon.beacon.doctype.beacon_alert.beacon_alert import BeaconAlert
from beacon.beacon.doctype.beacon_notification.beacon_notification import BeaconNotification
from beacon.core.alert.rules import BURN_KIND, AlertRules
from beacon.core.alert.states import OPEN_ALERT_STATUSES, PAGED_SEVERITIES, SEVERITIES, Severity
from beacon.core.exceptions import IncidentActionError
from beacon.core.incident.chat_message import ChatMessage
from beacon.core.incident.escalation import IncidentEscalation
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

		from beacon.beacon.doctype.beacon_incident_scope.beacon_incident_scope import BeaconIncidentScope
		from beacon.beacon.doctype.beacon_incident_service.beacon_incident_service import (
			BeaconIncidentService,
		)
		from beacon.beacon.doctype.beacon_incident_update.beacon_incident_update import BeaconIncidentUpdate

		acknowledged_at: DF.Datetime | None
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
		subscribers_html: DF.HTML | None
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
		for row in self.affected_services:
			if frappe.db.get_value("Beacon Alert Rule", row.service, "kind") != BURN_KIND:
				frappe.throw(_("Affected services must be SLO Burn rules."))

	def on_update(self) -> None:
		if self.visibility != "Internal":
			StatusCache.clear(self.public_id)
			IncidentSubscription.queue_new_updates(self)

	@document_action
	def acknowledge(self) -> None:
		self.check_permission("write")
		self.STATUS.require(self, *OPEN_STATUSES)
		escalation = IncidentEscalation(self)
		if self.acknowledged_by and not escalation.is_active:
			return

		if not self.acknowledged_by:
			self.acknowledged_by = frappe.session.user
			self.acknowledged_at = now_datetime()
		self.STATUS.move(self, "Investigating" if self.status == "Triggered" else self.status)
		escalation.stop()
		BeaconNotification.acknowledge_calls(self.name)
		self.save()

	@document_action
	def post_update(self, message: str, status: str | None = None, is_public: bool = False) -> None:
		self.check_permission("write")
		if self.type == "Maintenance":
			self.STATUS.require(self, "Scheduled", "In Progress")
		elif status == "Resolved":
			self.resolve(message, is_public)
			return
		else:
			self.STATUS.require(self, *OPEN_STATUSES)
			if status and status not in OPEN_STATUSES:
				frappe.throw(_("Choose Investigating, Identified or Monitoring."), IncidentActionError)
			self.STATUS.move(self, status or self.status)

		self.add_update(message, is_public)
		self.save()

	@document_action
	def resolve(self, message: str, is_public: bool = False, silence_hours: int = 0) -> None:
		"""Close the incident. Unsuppressed firing alerts block it unless they are silenced for silence_hours."""
		self.check_permission("write")
		self.STATUS.require(self, *OPEN_STATUSES)
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
		alerts = ChatMessage.alert_table(self._describe_alerts(), with_rule=True, with_lasted=True)
		BeaconNotification.announce(
			self, f"✅ **Resolved** {ChatMessage.link(self.name)}: {message}\n\n{alerts}", email=True
		)

	def _describe_alerts(self) -> list[frappe._dict]:
		"""The alerts of the incident as chat table rows. An alert still firing at resolve was silenced."""
		alerts = frappe.get_all(
			"Beacon Alert",
			filters={"incident": self.name},
			fields=[
				"rule.title as rule_title",
				"resource.title as resource_title",
				"resource.resource_id",
				"detail",
				"fired_at",
				"resolved_at",
			],
			order_by="fired_at asc",
		)
		for alert in alerts:
			alert.resource_title = alert.resource_title or alert.resource_id
			alert.lasted = (
				ChatMessage.duration(alert.fired_at, alert.resolved_at) if alert.resolved_at else "silenced"
			)
		return alerts

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
		self.STATUS.require(self, *OPEN_STATUSES)
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
		self.STATUS.require(self, *OPEN_STATUSES)
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

	@frappe.whitelist(methods=["GET"])
	@frappe.read_only()
	def get_overview(self) -> dict:
		"""The worst open alert, else the oldest alert, and the incident's alerts for the Overview tab."""
		self.check_permission("read")
		rows = self._get_overview_alerts()
		open_rows = [row for row in rows if row.status in OPEN_ALERT_STATUSES]
		cause = (
			max(open_rows, key=lambda row: SEVERITIES.index(row.severity))
			if open_rows
			else (rows[0] if rows else None)
		)
		if cause:
			cause.rule_title, cause.rule_description = AlertRules.get_problem_text(
				cause.rule_kind, cause.rule_title, cause.rule_description
			)
		fields = (
			"name",
			"title",
			"detail",
			"severity",
			"status",
			"started_at",
			"fired_at",
			"clean_since",
			"resolved_at",
			"resolution",
			"resource",
			"resource_title",
			"rule",
		)
		return {
			"cause": cause,
			"alerts": [
				{field: row.get(field) for field in fields}
				for row in sorted(rows, key=lambda row: row.status not in OPEN_ALERT_STATUSES)[:50]
			],
			"total": len(rows),
			"firing": sum(row.status == "Firing" for row in rows),
			"notifications": frappe.get_all(
				"Beacon Notification",
				filters={"incident": self.name},
				fields=["channel", "responder", "status", "sent_at", "answered_at"],
				order_by="creation desc",
				limit=30,
			),
			"history": self._get_history(rows),
			"cause_resource": cause.resource if cause else None,
			"chart_keys": AlertRules.get_chart_keys(cause.charts) if cause else [],
			"central_url": self._get_central_url(cause),
			"parent": self._get_parent(cause),
		}

	@staticmethod
	def _get_central_url(cause: frappe._dict | None) -> str | None:
		"""Central names its VMs by its own ID, so the link filters its list on the Atlas VM ID."""
		central_url = frappe.db.get_single_value("Beacon Settings", "central_url")
		if not (cause and central_url and cause.resource_type == "Virtual Machine"):
			return None
		return (
			f"{central_url.rstrip('/')}/desk/virtual-machine?{urlencode({'atlas_vm_id': cause.resource_id})}"
		)

	@staticmethod
	def _get_parent(cause: frappe._dict | None) -> dict | None:
		"""Where the cause resource runs now, such as the host of a VM. It can differ from where it ran during the incident."""
		if not (cause and cause.parent_resource):
			return None
		parent = frappe.db.get_value(
			"Beacon Resource",
			cause.parent_resource,
			["name", "title", "resource_id", "resource_type", "region", "url"],
			as_dict=True,
		)
		if parent:
			parent.firing = frappe.db.count("Beacon Alert", {"resource": parent.name, "status": "Firing"})
		return parent

	def _get_history(self, alerts: list[frappe._dict]) -> list[dict]:
		pairs = {(alert.rule, alert.resource) for alert in alerts if alert.rule and alert.resource}
		if not pairs:
			return []
		rows = frappe.get_all(
			"Beacon Alert",
			filters={
				"rule": ("in", sorted({rule for rule, _resource in pairs})),
				"resource": ("in", sorted({resource for _rule, resource in pairs})),
				"started_at": (">=", now_datetime() - timedelta(days=30)),
				"incident": ("!=", self.name),
			},
			fields=["rule", "resource", "started_at", "incident", "incident.title as incident_title"],
		)
		history: dict[tuple[str, str], dict] = {}
		for row in rows:
			pair = (row.rule, row.resource)
			if pair not in pairs:
				continue
			started = get_datetime(row.started_at)
			entry = history.setdefault(
				pair, {"count": 0, "last_started_at": started, "days": set(), "incidents": {}}
			)
			entry["count"] += 1
			entry["last_started_at"] = max(entry["last_started_at"], started)
			entry["days"].add(started.date())
			if row.incident:
				incident = entry["incidents"].setdefault(
					row.incident,
					{"name": row.incident, "title": row.incident_title, "started_at": started},
				)
				incident["started_at"] = min(incident["started_at"], started)

		return [
			{
				"rule": rule,
				"resource": resource,
				"count": entry["count"],
				"last_started_at": entry["last_started_at"],
				"days": len(entry["days"]),
				"incidents": sorted(
					entry["incidents"].values(), key=lambda item: item["started_at"], reverse=True
				)[:3],
			}
			for (rule, resource), entry in sorted(
				history.items(), key=lambda item: item[1]["last_started_at"], reverse=True
			)
		]

	def _get_overview_alerts(self) -> list[frappe._dict]:
		alert = frappe.qb.DocType("Beacon Alert")
		rule = frappe.qb.DocType("Beacon Alert Rule")
		resource = frappe.qb.DocType("Beacon Resource")
		return (
			frappe.qb.from_(alert)
			.left_join(rule)
			.on(rule.name == alert.rule)
			.left_join(resource)
			.on(resource.name == alert.resource)
			.select(
				alert.name,
				alert.title,
				alert.detail,
				alert.severity,
				alert.status,
				alert.started_at,
				alert.fired_at,
				alert.clean_since,
				alert.resolved_at,
				alert.value,
				alert.resolution,
				alert.region,
				alert.resource,
				rule.name.as_("rule"),
				rule.kind.as_("rule_kind"),
				rule.title.as_("rule_title"),
				rule.description.as_("rule_description"),
				rule.runbook_url,
				rule.scope_includes_descendants,
				rule.charts,
				Coalesce(resource.title, resource.resource_id).as_("resource_title"),
				resource.resource_id,
				resource.resource_type,
				resource.path,
				resource.url,
				resource.parent_resource,
			)
			.where(alert.incident == self.name)
			.orderby(alert.started_at)
			.orderby(alert.name)
		).run(as_dict=True)

	@frappe.whitelist()
	@frappe.read_only()
	def get_affected_resources(self) -> list[list[str]]:
		"""The resources in the scope and under the incident's alerts, read from the registry when asked, as CSV rows."""
		self.check_permission("read")
		alerts = self._get_overview_alerts()
		with_descendants = {alert.rule for alert in alerts if alert.scope_includes_descendants}
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
			rows += [
				[resource.get(column) for column in columns]
				for resource in sorted(registry.by_name.values(), key=lambda row: row.path or "")
				if any(match.covers(resource) for match in matches)
			]
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
		self.STATUS.require(self, "In Progress")
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
			BeaconNotification.announce(
				self,
				f"{ChatMessage.header(alert.severity, self.name, alert.region)}\n\n"
				f"**Severity raised from {self.severity} to {alert.severity}** by {row.rule_title}.\n\n"
				f"{ChatMessage.alert_table([row])}",
			)
			self.severity = alert.severity
		self.STATUS.move(self, "Investigating" if was_monitoring else self.status)
		if is_worse:
			self.add_update(
				f"Severity raised to {alert.severity}: {alert.title}. {alert.detail or ''}".strip(),
				is_public=False,
			)
		elif was_monitoring:
			self.add_update(
				f"Back to Investigating: {alert.title}. {alert.detail or ''}".strip(), is_public=False
			)
		if (is_worse or was_monitoring) and self.severity in PAGED_SEVERITIES:
			IncidentEscalation(self).start(wait=False)
		self.save(ignore_permissions=True)

	def close_as_merged(self, target: str) -> None:
		self._release_alerts(target)
		self.merged_into = target
		self.STATUS.move(self, "Merged")
		self.close()
		self.save(ignore_permissions=True)
		BeaconNotification.announce(
			self, f"🔀 {ChatMessage.link(self.name)} **merged into** {ChatMessage.link(target)}"
		)

	def record_recovery(self) -> None:
		"""Beacon's action when no alert of the incident is open any more. Beacon resolves an Internal
		incident that it made. A person resolves every other incident, so Beacon posts to chat one time."""
		if (
			not self.is_open
			or self.alerts_recovered_at
			or frappe.db.exists(
				"Beacon Alert", {"incident": self.name, "status": ("in", OPEN_ALERT_STATUSES)}
			)
		):
			return

		self.alerts_recovered_at = now_datetime()
		if self.visibility == "Internal" and self.created_by_beacon:
			self.resolve("All alerts recovered.")
			return

		self.save(ignore_permissions=True)
		BeaconNotification.announce(
			self,
			f"⏸️ **All alerts recovered on {ChatMessage.link(self.name)}.** Check the service, then resolve the incident.",
		)

	def close(self) -> None:
		"""Stop the notifications and free the open alerts for a new incident. Does not save."""
		self.resolved_at = now_datetime()
		IncidentEscalation(self).stop()
		self._release_alerts(None)

	def _release_alerts(self, target: str | None) -> None:
		frappe.db.set_value(
			"Beacon Alert",
			{"incident": self.name, "status": ("in", OPEN_ALERT_STATUSES)},
			"incident",
			target,
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

	def require_published(self) -> None:
		if self.visibility not in PUBLISHED_VISIBILITIES or not self.public_id:
			frappe.throw(_("Only a Public or Unlisted incident has a link."), IncidentActionError)

	@staticmethod
	def open_for(alert: BeaconAlert, resource: frappe._dict) -> BeaconIncident:
		"""Beacon's action: open an Internal incident for a firing alert that no open incident takes. The
		on-call notification waits when the resource has a parent, so an incident of the parent can take
		the alert first."""
		rule = frappe.get_cached_value(
			"Beacon Alert Rule",
			alert.rule,
			["kind", "title", "description", "scope_includes_descendants"],
			as_dict=True,
		)
		problem_title, _ = AlertRules.get_problem_text(rule.kind, rule.title, rule.description)
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
		)
		incident.add_update(f"Opened: {alert.title}. {alert.detail or ''}".strip(), is_public=False)
		incident.insert(ignore_permissions=True)
		alert.join(incident.name)

		BeaconNotification.announce(
			incident,
			f"{ChatMessage.header(incident.severity, incident.name, alert.region)}\n\n"
			f"**{problem_title}**\n\n{ChatMessage.alert_table([alert.describe()])}",
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
		recovered = {alert.incident for alert in alerts} - {
			alert.incident for alert in alerts if alert.status in OPEN_ALERT_STATUSES
		}

		RecordTransaction.run_each(
			"Beacon Incident",
			sorted(recovered),
			"Beacon could not close a recovered incident",
			lambda name: frappe.get_doc("Beacon Incident", name, for_update=True).record_recovery(),
		)

	@staticmethod
	def advance_maintenances() -> None:
		now = now_datetime()
		for status, time_field, method in (
			("Scheduled", "scheduled_start", "start_maintenance"),
			("In Progress", "scheduled_end", "complete_maintenance"),
		):
			names = frappe.get_all(
				"Beacon Incident",
				filters={"type": "Maintenance", "status": status, time_field: ("<=", now)},
				pluck="name",
			)

			def run(name: str, status: str = status, method: str = method) -> None:
				maintenance = frappe.get_doc("Beacon Incident", name, for_update=True)
				if maintenance.status == status:
					getattr(maintenance, method)()

			RecordTransaction.run_each(
				"Beacon Incident", names, "Beacon could not advance a maintenance", run
			)


def on_doctype_update() -> None:
	frappe.db.add_index("Beacon Incident", ["status", "type"])
	frappe.db.add_index("Beacon Incident", ["visibility", "status"])
