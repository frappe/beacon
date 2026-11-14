from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.utils import now_datetime

from beacon.core.incident.notifiers import NOTIFIERS
from beacon.core.jobs import queue_notification
from beacon.core.transaction import RecordTransaction

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


class NotificationDelivery:
	"""Delivers one outbox record and records the provider result."""

	def __init__(self, notification: str):
		self.notification = frappe.get_doc("Beacon Notification", notification)

	def send(self) -> None:
		"""Send one queued notification. A failure is recorded on the row, not retried.

		The provider call runs before the row is locked. Only the write is retried, so a lock conflict
		cannot send the notification twice.
		"""
		notification = self.notification
		if notification.status != "Queued":
			return

		try:
			notifier = NOTIFIERS[notification.channel](frappe.get_cached_doc("Beacon Settings"))
			values = {
				"provider_ref": notifier.send(notification),
				"status": "Sent",
				"sent_at": now_datetime(),
			}
		except Exception as error:
			values = {"status": "Failed", "error": str(error)[:1000]}
			frappe.log_error(
				f"Beacon {notification.channel} notification failed",
				reference_doctype="Beacon Notification",
				reference_name=notification.name,
			)

		RecordTransaction("Beacon Notification", notification.name).retry(lambda: self._record(values))

	def _record(self, values: dict) -> None:
		"""A phone callback can set the status first. Then only the provider reference is kept."""
		status = frappe.db.get_value("Beacon Notification", self.notification.name, "status", for_update=True)
		if status != "Queued":
			values = {"provider_ref": values.get("provider_ref"), "sent_at": values.get("sent_at")}
		frappe.db.set_value("Beacon Notification", self.notification.name, values)

		# A failed page must not stay silent. When chat itself fails, only the Error Log has it.
		notification = self.notification
		if values.get("status") == "Failed" and notification.channel != "Chat":
			NotificationOutbox().post_to_chat(
				f"Beacon could not send the {notification.channel} notification {notification.name}"
				f" to {notification.responder} for {notification.incident}: {values['error'][:500]}"
			)
