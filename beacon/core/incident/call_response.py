from __future__ import annotations

import hmac

import frappe
from frappe.utils import now_datetime

from beacon.core.incident.merge import IncidentMerge
from beacon.core.incident.notifiers import CallNotifier
from beacon.core.transaction import RecordTransaction

CALL_STATUSES = {
	"in-progress": "Answered",
	"completed": "Answered",
	"busy": "No Answer",
	"no-answer": "No Answer",
	"failed": "Failed",
	"canceled": "Failed",
}


class CallResponse:
	"""Applies a Twilio call status to its notification. An answered call acknowledges the incident."""

	def __init__(self, notification: str):
		self.notification = notification

	def is_valid_token(self, token: str) -> bool:
		return hmac.compare_digest(CallNotifier.get_callback_token(self.notification), token)

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
			transaction.retry(self._acknowledge)
		else:
			transaction.retry(lambda: self._record_status(CALL_STATUSES[status]))

	def _acknowledge(self) -> None:
		"""Lock the incident before the notification, in the same order as every other incident action."""
		row = frappe.db.get_value(
			"Beacon Notification", self.notification, ["incident", "responder", "status"], as_dict=True
		)
		if not row or not row.incident or row.status == "Acknowledged":
			return

		incident = frappe.get_doc(
			"Beacon Incident", IncidentMerge.find_final_incident(row.incident), for_update=True
		)
		previous_user = frappe.session.user
		try:
			frappe.set_user(row.responder)  # nosemgrep: the answered call acts as the called responder
			incident.acknowledge()
			frappe.db.set_value(
				"Beacon Notification",
				self.notification,
				{"status": "Acknowledged", "answered_at": now_datetime()},
			)
		finally:
			frappe.set_user(previous_user)  # nosemgrep

	def _record_status(self, status: str) -> None:
		current = frappe.db.get_value("Beacon Notification", self.notification, "status", for_update=True)
		if current and current != "Acknowledged":
			frappe.db.set_value("Beacon Notification", self.notification, "status", status)
