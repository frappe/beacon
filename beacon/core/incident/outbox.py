from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import frappe
from frappe.utils import get_fullname, get_url_to_form, now_datetime

from beacon.core.incident.chat_message import ChatMessage
from beacon.core.incident.notifiers import NOTIFIERS, Notifier
from beacon.core.jobs import queue_notification
from beacon.core.transaction import RecordTransaction

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident

# A queued notification that no job took is queued again after the first time, and fails after the second.
REQUEUE_AFTER = timedelta(minutes=1)
FAIL_AFTER = timedelta(hours=1)


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

	def requeue_waiting(self) -> None:
		"""Scheduled every minute: a notification never depends on one job. A lost job is queued again,
		which is safe because the job claims the row first."""
		now = now_datetime()
		frappe.db.set_value(
			"Beacon Notification",
			{"status": "Queued", "creation": ("<", now - FAIL_AFTER)},
			{"status": "Failed", "error": "No worker sent it for 1 hour."},
		)
		for name in frappe.get_all(
			"Beacon Notification",
			filters={"status": "Queued", "creation": ("<", now - REQUEUE_AFTER)},
			pluck="name",
		):
			queue_notification(name)

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

		The row is claimed first, so a second job for it sends nothing. The provider call runs after that commit,
		with no lock held. A job that stops during the call leaves the row in Sending, and nobody resends it,
		because the provider may already have it.
		"""
		notification = self.notification
		notifier = NOTIFIERS[notification.channel](frappe.get_cached_doc("Beacon Settings"))
		if not RecordTransaction("Beacon Notification", notification.name).retry(
			lambda: self._claim(notifier)
		):
			return

		try:
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

	def _claim(self, notifier: Notifier) -> bool:
		"""Move the row from Queued to Sending. A pause, a disabled channel or a disabled responder that came
		after the row was queued cancels it. An escalation pages again when paging resumes."""
		name = self.notification.name
		if frappe.db.get_value("Beacon Notification", name, "status", for_update=True) != "Queued":
			return False

		responder = self.notification.responder
		if not notifier.is_enabled() or (responder and not frappe.db.get_value("User", responder, "enabled")):
			frappe.db.set_value(
				"Beacon Notification",
				name,
				{
					"status": "Cancelled",
					"error": "Calls and emails were paused, or the channel or responder was disabled.",
				},
			)
			return False

		frappe.db.set_value("Beacon Notification", name, "status", "Sending")
		return True

	def _record(self, values: dict) -> None:
		"""A phone callback can set the status first. Then only the provider reference is kept."""
		status = frappe.db.get_value("Beacon Notification", self.notification.name, "status", for_update=True)
		if status != "Sending":
			values = {"provider_ref": values.get("provider_ref"), "sent_at": values.get("sent_at")}
		frappe.db.set_value("Beacon Notification", self.notification.name, values)

		# A failed notification must not stay silent. When chat itself fails, only the Error Log has it.
		# The error stays on the row: provider responses can hold data that chat must not show.
		notification = self.notification
		if values.get("status") == "Failed" and notification.channel != "Chat":
			NotificationOutbox().queue(
				"Chat",
				f"⚠️ **Could not {'call' if notification.channel == 'Call' else 'email'}**"
				f" {get_fullname(notification.responder)}"
				f"{f' for {ChatMessage.link(notification.incident)}' if notification.incident else ''}."
				f" Contact them directly. [Error]({get_url_to_form('Beacon Notification', notification.name)})",
				notification.incident,
			)
