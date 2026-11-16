from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from beacon.beacon.doctype.beacon_silence.beacon_silence import BeaconSilence
from beacon.core.exceptions import IncidentActionError
from beacon.core.incident.chat_message import ChatMessage
from beacon.core.incident.escalation import IncidentEscalation
from beacon.core.incident.lifecycle import IncidentLifecycle
from beacon.core.incident.merge import IncidentMerge
from beacon.core.incident.outbox import NotificationOutbox
from beacon.core.incident.postmortem import Postmortem
from beacon.core.incident.publication import IncidentPublication
from beacon.core.incident.subscription import IncidentSubscription
from beacon.core.resource.registry import ResourceMatch, ResourceRegistry
from beacon.core.status_page import StatusCache
from beacon.core.transaction import document_action

REOPEN_DAYS = 7


class BeaconIncident(Document):
	"""An incident or a maintenance. State fields change only through these methods."""

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
		created_by_beacon: DF.Check
		detected_by: DF.Literal["Alert", "Engineer", "Customer"]
		escalation: DF.Literal["None", "Waiting", "Calling", "Done"]
		excluded_from_slo: DF.Check
		group_keys: DF.SmallText | None
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

	@property
	def lifecycle(self) -> IncidentLifecycle:
		return IncidentLifecycle(self)

	@document_action
	def acknowledge(self) -> None:
		self.check_permission("write")
		self.lifecycle.require_open()
		if self.acknowledged_by:
			return

		self.acknowledged_by = frappe.session.user
		self.acknowledged_at = now_datetime()
		if self.status == "Triggered":
			self.lifecycle.move_to("Investigating")
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
			self.lifecycle.require("Scheduled", "In Progress")
		elif status == "Resolved":
			self.resolve(message, is_public)
			return
		else:
			self.lifecycle.require_open()
			if status and status != self.status:
				self.lifecycle.move_to(status)

		self.add_update(message, is_public)
		self.save()

	@document_action
	def resolve(self, message: str, is_public: bool = False, silence_hours: int = 0) -> None:
		"""Close the incident. Unsuppressed firing alerts block it unless they are silenced for silence_hours."""
		self.check_permission("write")
		self.lifecycle.require_open()
		firing = frappe.get_all(
			"Beacon Alert",
			filters={"incident": self.name, "status": "Firing", "suppressed": 0},
			fields=["name", "region", "resource", "rule", "alert_key"],
		)
		if firing and not silence_hours:
			frappe.throw(
				_(
					"{0} alerts still fire. Fix the problem first, or enter hours to silence these alerts."
				).format(len(firing)),
				IncidentActionError,
			)

		for alert in firing:
			BeaconSilence.create_for_alert(
				alert, silence_hours, f"Resolved {self.name} with the alert still firing."
			)
		self.add_update(message, is_public, status="Resolved")
		self.lifecycle.move_to("Resolved")
		self.save()
		alerts = ChatMessage.alert_table(self._describe_alerts(), with_rule=True, with_lasted=True)
		NotificationOutbox().announce(
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

		self.lifecycle.move_to("Investigating")
		self.resolved_at = None
		self.add_update("Reopened.", is_public=False)
		self.save()

	@document_action
	def page_on_call(self) -> None:
		self.check_permission("write")
		self.lifecycle.require_open()
		escalation = IncidentEscalation(self)
		if escalation.is_active:
			frappe.throw(
				_("Beacon already notifies the on-call team about this incident."), IncidentActionError
			)
		escalation.start(wait=False)

	@document_action
	def merge_into(self, target: str) -> None:
		self.check_permission("write")
		self.lifecycle.require_open()
		IncidentMerge(self, target).merge()

	@document_action
	def publish(self, visibility: str, message: str, affected_services: list[dict] | None = None) -> None:
		self.check_permission("write")
		IncidentPublication(self).publish(visibility, message, affected_services)

	@document_action
	def change_visibility(self, visibility: str) -> None:
		self.check_permission("write")
		IncidentPublication(self).change_visibility(visibility)

	@document_action
	def regenerate_public_link(self) -> None:
		self.check_permission("write")
		IncidentPublication(self).regenerate_public_link()

	@frappe.whitelist()
	@frappe.read_only()
	def get_public_link(self) -> str:
		self.check_permission("read")
		return IncidentPublication(self).get_public_link()

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
		self.lifecycle.move_to("In Progress")
		self.save()

	@document_action
	def extend_maintenance(self, scheduled_end: str) -> None:
		self.check_permission("write")
		self.lifecycle.require("In Progress")
		if get_datetime(scheduled_end) <= max(now_datetime(), get_datetime(self.scheduled_end)):
			frappe.throw(_("Choose an end time later than the current end."), IncidentActionError)
		self.scheduled_end = scheduled_end
		self.save()

	@document_action
	def complete_maintenance(self) -> None:
		self.check_permission("write")
		self.lifecycle.move_to("Completed")
		self.save()

	@document_action
	def cancel_maintenance(self) -> None:
		self.check_permission("write")
		self.lifecycle.move_to("Cancelled")
		self.save()

	def retrigger(self) -> None:
		"""Beacon reopens a recently resolved incident when its alert group fires again."""
		self.lifecycle.move_to("Triggered")
		self.resolved_at = None
		self.acknowledged_by = None
		self.acknowledged_at = None
		self.save(ignore_permissions=True)
		NotificationOutbox().announce(self, f"🔁 **Reopened** {ChatMessage.link(self.name)}: {self.title}")

	def add_group_key(self, key: str) -> None:
		keys = (self.group_keys or "").split()
		if key not in keys:
			self.group_keys = "\n".join([*keys, key])

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


def on_doctype_update() -> None:
	frappe.db.add_index("Beacon Incident", ["status", "type"])
	frappe.db.add_index("Beacon Incident", ["visibility", "status"])
