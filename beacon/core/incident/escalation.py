from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import frappe
from frappe.utils import get_datetime, now_datetime

from beacon.beacon.doctype.beacon_notification.beacon_notification import BeaconNotification
from beacon.core.incident.chat_message import ChatMessage
from beacon.core.transaction import RecordTransaction

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident


ACTIVE_ESCALATIONS = ("Waiting", "Calling")


class IncidentEscalation:
	"""Calls the responders of one incident in order, round after round, until someone acknowledges."""

	def __init__(self, incident: BeaconIncident):
		self.incident = incident
		self.settings = frappe.get_cached_doc("Beacon Settings")
		self.now = now_datetime()

	@staticmethod
	def step_due() -> None:
		"""Step every escalation whose time has passed. Each incident is locked and committed alone."""
		now = now_datetime()
		names = frappe.get_all(
			"Beacon Incident",
			filters={"escalation": ("in", ACTIVE_ESCALATIONS), "next_page_at": ("<=", now)},
			pluck="name",
		)
		RecordTransaction.run_each(
			"Beacon Incident", names, "Beacon escalation step failed", IncidentEscalation._step
		)

	@staticmethod
	def _step(name: str) -> None:
		"""Check again under the lock: an acknowledge or another step may have run since the query."""
		escalation = IncidentEscalation(frappe.get_doc("Beacon Incident", name, for_update=True))
		next_page_at = escalation.incident.next_page_at
		if escalation.is_active and next_page_at and get_datetime(next_page_at) <= escalation.now:
			escalation.step()

	@property
	def is_active(self) -> bool:
		return self.incident.escalation in ACTIVE_ESCALATIONS

	def start(self, wait: bool) -> None:
		"""Start with the first responder, at once or after the page delay. Does not save."""
		self.incident.page_round = 1
		self.incident.page_index = 0
		if wait:
			self.incident.escalation = "Waiting"
			self.incident.next_page_at = self.now + timedelta(seconds=self.settings.page_delay_seconds)
		else:
			self.call_next()

	def step(self) -> None:
		"""After the page delay, call the first responder. While calling, move on to the next one."""
		if self.settings.paging_paused:
			return
		if self.incident.escalation == "Calling":
			self.move_to_next_responder()
		self.call_next()
		self.incident.db_set(
			{
				"escalation": self.incident.escalation,
				"next_page_at": self.incident.next_page_at,
				"page_round": self.incident.page_round,
				"page_index": self.incident.page_index,
			}
		)

	def stop(self) -> None:
		"""Called on acknowledge and close. Does not save."""
		if self.is_active:
			self.incident.escalation = "Done"
		self.incident.next_page_at = None

	def move_to_next_responder(self) -> None:
		self.incident.page_index += 1
		if self.incident.page_index >= len(self.settings.get_active_responders()):
			self.incident.page_index = 0
			self.incident.page_round += 1

	def call_next(self) -> None:
		"""Call and mail the current responder, then plan the next step."""
		responders = self.settings.get_active_responders()
		if not responders:
			self._finish("No responder is active. Contact the team directly.")
			return

		self.incident.escalation = "Calling"
		if self.incident.page_index >= len(responders):
			self.incident.page_index = 0
		responder = responders[self.incident.page_index]
		message = f"{self.incident.severity} incident {self.incident.name}: {self.incident.title}"
		for channel in ("Call", "Email"):
			if channel == "Email" and self._has_emailed(responder.user):
				continue
			BeaconNotification.queue(
				channel, message, self.incident.name, responder.user, self.incident.page_round
			)

		is_last = self.incident.page_index == len(responders) - 1
		minutes = (
			self.settings.repeat_every_minutes
			if is_last
			else self.settings.get_backup_after_minutes(self.incident.severity)
		)
		self.incident.next_page_at = self.now + timedelta(minutes=minutes)

	def _has_emailed(self, responder: str) -> bool:
		"""One escalation email for each responder in each round. A cancelled or failed email was not sent."""
		return bool(
			frappe.db.get_value(
				"Beacon Notification",
				{
					"incident": self.incident.name,
					"responder": responder,
					"page_round": self.incident.page_round,
					"channel": "Email",
					"status": ("in", ("Queued", "Sending", "Sent")),
				},
				"name",
			)
		)

	def _finish(self, reason: str) -> None:
		self.incident.escalation = "Done"
		self.incident.next_page_at = None
		BeaconNotification.announce(
			self.incident,
			f"⛔ **Calls stopped for {ChatMessage.link(self.incident.name)}.** {reason}",
		)
