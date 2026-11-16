from __future__ import annotations

from typing import TYPE_CHECKING

import frappe

from beacon.core.incident.notifiers import NOTIFIERS
from beacon.core.jobs import queue_notification

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident


class NotificationOutbox:
	"""Writes notifications in the caller's transaction. A job sends each one after the commit."""

	def __init__(self):
		self.settings = frappe.get_cached_doc("Beacon Settings")

	def is_enabled(self, channel: str) -> bool:
		return NOTIFIERS[channel](self.settings).is_enabled()

	def queue(
		self,
		channel: str,
		message: str,
		incident: str | None = None,
		responder: str | None = None,
		page_round: int = 0,
	) -> None:
		"""Queue one notification. A disabled channel drops it, as the settings ask."""
		if not self.is_enabled(channel):
			return

		notification = frappe.get_doc(
			{
				"doctype": "Beacon Notification",
				"incident": incident,
				"channel": channel,
				"responder": responder,
				"message": message,
				"page_round": page_round,
				"status": "Queued",
			}
		).insert(ignore_permissions=True)
		queue_notification(notification.name)

	def post_to_chat(self, message: str) -> None:
		self.queue("Chat", message)

	def announce(self, incident: BeaconIncident, message: str, email: bool = False) -> None:
		"""Post to chat. With email, also mail every active responder unless the severity is Low."""
		self.queue("Chat", message, incident.name)
		if not email or incident.severity == "Low":
			return

		for responder in self.settings.get_active_responders():
			self.queue("Email", message, incident.name, responder=responder.user)
