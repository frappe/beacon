from __future__ import annotations

import hmac
from datetime import timedelta
from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, get_fullname, get_url_to_form, now_datetime

from beacon.core.incident.chat_message import ChatMessage
from beacon.core.incident.notifiers import NOTIFIERS, CallNotifier, Notifier
from beacon.core.jobs import queue_notification
from beacon.core.state_machine import StateMachine
from beacon.core.transaction import RecordTransaction

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_incident.beacon_incident import BeaconIncident

CALL_STATUSES = {
	"in-progress": "Answered",
	"completed": "Answered",
	"busy": "No Answer",
	"no-answer": "No Answer",
	"failed": "Failed",
	"canceled": "Failed",
}
# Twilio sends the last status of a call within minutes. A later callback is a replay.
CALLBACK_WINDOW = timedelta(hours=1)
# A merge that lands while a callback waits for the lock is followed again, this many times.
MERGE_ATTEMPTS = 3
# A queued notification that no job took is queued again after the first time, and fails after the second.
REQUEUE_AFTER = timedelta(minutes=1)
FAIL_AFTER = timedelta(hours=1)


class BeaconNotification(Document):
	"""One call, email or chat post. Written as Queued and sent by a job after the commit."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		answered_at: DF.Datetime | None
		channel: DF.Literal["Call", "Email", "Chat"]
		error: DF.SmallText | None
		incident: DF.Link | None
		message: DF.SmallText | None
		page_round: DF.Int
		provider_ref: DF.Data | None
		responder: DF.Link | None
		sent_at: DF.Datetime | None
		status: DF.Literal[
			"Queued", "Sending", "Sent", "Answered", "No Answer", "Acknowledged", "Failed", "Cancelled"
		]
	# end: auto-generated types

	STATUS = StateMachine(
		"status",
		{
			"Queued": ("Sending", "Cancelled", "Failed", "Acknowledged"),
			"Sending": ("Sent", "Answered", "No Answer", "Failed", "Acknowledged"),
			"Sent": ("Answered", "No Answer", "Failed", "Acknowledged"),
			"Answered": ("Acknowledged",),
		},
	)

	@staticmethod
	def is_enabled(channel: str) -> bool:
		return NOTIFIERS[channel](frappe.get_cached_doc("Beacon Settings")).is_enabled()

	@staticmethod
	def queue(
		channel: str,
		message: str,
		incident: str | None = None,
		responder: str | None = None,
		page_round: int = 0,
	) -> None:
		"""Write one notification in the caller's transaction. A disabled channel drops it."""
		if not BeaconNotification.is_enabled(channel):
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

	@staticmethod
	def post_to_chat(message: str) -> None:
		BeaconNotification.queue("Chat", message)

	@staticmethod
	def announce(incident: BeaconIncident, message: str, email: bool = False) -> None:
		"""Post to chat. With email, also mail every active responder unless the severity is Low."""
		BeaconNotification.queue("Chat", message, incident.name)
		if not email or incident.severity == "Low":
			return

		for responder in frappe.get_cached_doc("Beacon Settings").get_active_responders():
			BeaconNotification.queue("Email", message, incident.name, responder=responder.user)

	@staticmethod
	def report_failed_delivery(notification: BeaconNotification) -> None:
		"""Tell chat that a call or email failed, with a link to the error on the notification."""
		action = "call" if notification.channel == "Call" else "email"
		incident = f" for {ChatMessage.link(notification.incident)}" if notification.incident else ""
		BeaconNotification.queue(
			"Chat",
			f"⚠️ **Could not {action} {get_fullname(notification.responder)}{incident}.**"
			f" Contact them directly. [Details]({get_url_to_form('Beacon Notification', notification.name)})",
			notification.incident,
		)

	def send(self) -> None:
		"""Send one queued notification. A failure is recorded on the row, not retried.

		The row is claimed first, so a second job for it sends nothing. The provider call runs after that commit,
		with no lock held. A job that stops during the call leaves the row in Sending, and nobody resends it,
		because the provider may already have it.
		"""
		notifier = NOTIFIERS[self.channel](frappe.get_cached_doc("Beacon Settings"))
		if not RecordTransaction("Beacon Notification", self.name).retry(lambda: self._claim(notifier)):
			return

		try:
			values = {
				"provider_ref": notifier.send(self),
				"status": "Sent",
				"sent_at": now_datetime(),
			}
		except Exception as error:
			values = {"status": "Failed", "error": str(error)[:1000]}
			frappe.log_error(
				f"Beacon {self.channel} notification failed",
				reference_doctype="Beacon Notification",
				reference_name=self.name,
			)

		RecordTransaction("Beacon Notification", self.name).retry(lambda: self._record(values))

	def _claim(self, notifier: Notifier) -> bool:
		"""Move the row from Queued to Sending. A pause, a disabled channel or a disabled responder that came
		after the row was queued cancels it. An escalation pages again when paging resumes."""
		notification = frappe.get_doc("Beacon Notification", self.name, for_update=True)
		if notification.status != "Queued":
			return False

		if not notifier.is_enabled() or (
			notification.responder and not frappe.db.get_value("User", notification.responder, "enabled")
		):
			notification.STATUS.move(notification, "Cancelled")
			notification.error = "Calls and emails were paused, or the channel or responder was disabled."
			notification.save(ignore_permissions=True)
			return False

		notification.STATUS.move(notification, "Sending")
		notification.save(ignore_permissions=True)
		return True

	def _record(self, values: dict) -> None:
		"""A phone callback can set the status first. Then only the provider reference is kept."""
		notification = frappe.get_doc("Beacon Notification", self.name, for_update=True)
		failed = notification.status == "Sending" and values["status"] == "Failed"
		if notification.status == "Sending":
			notification.STATUS.move(notification, values["status"])
			notification.error = values.get("error")
		notification.provider_ref = values.get("provider_ref")
		notification.sent_at = values.get("sent_at")
		notification.save(ignore_permissions=True)

		# A failed notification must not stay silent. When chat itself fails, only the Error Log has it.
		# The error stays on the row: provider responses can hold data that chat must not show.
		if failed and notification.channel != "Chat":
			BeaconNotification.report_failed_delivery(notification)

	def is_valid_callback(self, token: str, account_sid: str, call_sid: str) -> bool:
		"""The token proves that Beacon made the URL. The other checks keep a leaked URL to its own call and to
		a short time, because nothing signs the request body."""
		return bool(
			self.channel == "Call"
			and hmac.compare_digest(CallNotifier.get_callback_token(self.name), token)
			and account_sid == frappe.get_cached_doc("Beacon Settings").twilio_account_sid
			and self.provider_ref in (None, "", call_sid)
			and now_datetime() - get_datetime(self.creation) < CALLBACK_WINDOW
		)

	def record_call_status(self, twilio_status: str) -> None:
		if twilio_status not in CALL_STATUSES:
			frappe.log_error(
				f"Beacon got an unknown Twilio call status {twilio_status}",
				reference_doctype="Beacon Notification",
				reference_name=self.name,
			)
			return

		transaction = RecordTransaction("Beacon Notification", self.name)
		if CALL_STATUSES[twilio_status] == "Answered":
			transaction.retry(self._answer)
		else:
			transaction.retry(lambda: self._record_call_status(CALL_STATUSES[twilio_status], twilio_status))

	def _answer(self) -> None:
		"""Lock the incident before the notification, in the same order as every other incident action. A call
		answered after the incident closed acknowledges nothing. The first answer time stays."""
		name = frappe.db.get_value("Beacon Notification", self.name, "incident")
		incident = self._lock_open_incident(name) if name else None
		notification = frappe.get_doc("Beacon Notification", self.name, for_update=True)
		target = "Acknowledged" if incident else "Answered"
		if not notification.STATUS.can_move(notification, target):
			return

		if incident:
			self._acknowledge_as(incident, notification.responder)
		notification.STATUS.move(notification, target)
		frappe.db.set_value(
			"Beacon Notification",
			self.name,
			{"status": notification.status, "answered_at": notification.answered_at or now_datetime()},
		)

	@staticmethod
	def _lock_open_incident(name: str) -> BeaconIncident | None:
		for _attempt in range(MERGE_ATTEMPTS):
			name = frappe.get_doc("Beacon Incident", name).get_final_incident()
			incident = frappe.get_doc("Beacon Incident", name, for_update=True)
			if not incident.merged_into:
				return incident if incident.is_open else None
		return None

	@staticmethod
	def _acknowledge_as(incident: BeaconIncident, responder: str) -> None:
		previous_user = frappe.session.user
		try:
			frappe.set_user(responder)  # nosemgrep: the answered call acts as the called responder
			incident.acknowledge()
		finally:
			frappe.set_user(previous_user)  # nosemgrep

	def _record_call_status(self, status: str, twilio_status: str) -> None:
		"""A failed call posts to chat once. A late status never undoes an acknowledgement."""
		notification = frappe.get_doc("Beacon Notification", self.name, for_update=True)
		if not notification.STATUS.can_move(notification, status):
			return

		notification.STATUS.move(notification, status)
		if status == "Failed":
			notification.error = f"Twilio reported the call as {twilio_status}."
			BeaconNotification.report_failed_delivery(notification)
		notification.save(ignore_permissions=True)

	@staticmethod
	def requeue_waiting() -> None:
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

	@staticmethod
	def acknowledge_calls(incident: str) -> None:
		"""A person acknowledged the incident, so its open calls are acknowledged too."""
		frappe.db.set_value(
			"Beacon Notification",
			{
				"incident": incident,
				"channel": "Call",
				"status": ("in", ("Queued", "Sending", "Sent", "Answered")),
			},
			"status",
			"Acknowledged",
		)


def on_doctype_update() -> None:
	frappe.db.add_index("Beacon Notification", ["incident", "sent_at"])
	frappe.db.add_index("Beacon Notification", ["provider_ref"])
