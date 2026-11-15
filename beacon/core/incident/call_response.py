from __future__ import annotations

import hmac
from datetime import timedelta
from typing import TYPE_CHECKING

import frappe
from frappe.utils import get_datetime, now_datetime

from beacon.core.incident.merge import IncidentMerge
from beacon.core.incident.notifiers import CallNotifier
from beacon.core.incident.outbox import NotificationOutbox
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


class CallResponse:
	"""Applies a Twilio call status to its notification. An answered call acknowledges the incident."""

	def __init__(self, notification: str):
		self.notification = notification

	def is_valid_callback(self, token: str, account_sid: str, call_sid: str) -> bool:
		"""The token proves that Beacon made the URL. The other checks keep a leaked URL to its own call and to
		a short time, because nothing signs the request body."""
		row = frappe.db.get_value(
			"Beacon Notification", self.notification, ["channel", "provider_ref", "creation"], as_dict=True
		)
		return bool(
			row
			and row.channel == "Call"
			and hmac.compare_digest(CallNotifier.get_callback_token(self.notification), token)
			and account_sid == frappe.get_cached_doc("Beacon Settings").twilio_account_sid
			and row.provider_ref in (None, "", call_sid)
			and now_datetime() - get_datetime(row.creation) < CALLBACK_WINDOW
		)

	def record_status(self, status: str) -> None:
		if status not in CALL_STATUSES:
			frappe.log_error(
				f"Beacon got an unknown Twilio call status {status}",
				reference_doctype="Beacon Notification",
				reference_name=self.notification,
			)
			return

		transaction = RecordTransaction("Beacon Notification", self.notification)
		if CALL_STATUSES[status] == "Answered":
			transaction.retry(self._answer)
		else:
			transaction.retry(lambda: self._record_status(CALL_STATUSES[status], status))

	def _answer(self) -> None:
		"""Lock the incident before the notification, in the same order as every other incident action. A call
		answered after the incident closed acknowledges nothing. The first answer time stays."""
		name = frappe.db.get_value("Beacon Notification", self.notification, "incident")
		incident = self._lock_open_incident(name) if name else None
		row = frappe.db.get_value(
			"Beacon Notification",
			self.notification,
			["responder", "status", "answered_at"],
			as_dict=True,
			for_update=True,
		)
		if not row or row.status == "Acknowledged":
			return

		if incident:
			self._acknowledge_as(incident, row.responder)
		frappe.db.set_value(
			"Beacon Notification",
			self.notification,
			{
				"status": "Acknowledged" if incident else "Answered",
				"answered_at": row.answered_at or now_datetime(),
			},
		)

	@staticmethod
	def _lock_open_incident(name: str) -> BeaconIncident | None:
		for _attempt in range(MERGE_ATTEMPTS):
			incident = frappe.get_doc(
				"Beacon Incident", IncidentMerge.find_final_incident(name), for_update=True
			)
			if not incident.merged_into:
				return incident if incident.lifecycle.is_open else None
			name = incident.merged_into
		return None

	@staticmethod
	def _acknowledge_as(incident: BeaconIncident, responder: str) -> None:
		previous_user = frappe.session.user
		try:
			frappe.set_user(responder)  # nosemgrep: the answered call acts as the called responder
			incident.acknowledge()
		finally:
			frappe.set_user(previous_user)  # nosemgrep

	def _record_status(self, status: str, twilio_status: str) -> None:
		"""A failed call posts to chat once. A later status never undoes an acknowledgement."""
		row = frappe.db.get_value(
			"Beacon Notification",
			self.notification,
			["status", "responder", "incident"],
			as_dict=True,
			for_update=True,
		)
		if not row or row.status in ("Acknowledged", status):
			return

		values = {"status": status}
		if status == "Failed":
			values["error"] = f"Twilio reported the call as {twilio_status}."
			NotificationOutbox().post_to_chat(
				f"Beacon could not call {row.responder} for {row.incident}: {values['error']}"
			)
		frappe.db.set_value("Beacon Notification", self.notification, values)
