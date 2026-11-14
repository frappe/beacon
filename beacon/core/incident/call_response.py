from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from beacon.core.incident.merge import IncidentMerge
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
	"""Applies a verified phone response to its notification and incident."""

	def __init__(self, notification: str):
		self.notification = notification

	@classmethod
	def from_call_sid(cls, call_sid: str) -> CallResponse | None:
		name = frappe.db.get_value("Beacon Notification", {"provider_ref": call_sid}, "name")
		if not name:
			frappe.log_error("Beacon got a Twilio status for an unknown call", call_sid)
			return None
		return cls(name)

	def get_message(self) -> str:
		return frappe.db.get_value("Beacon Notification", self.notification, "message") or "Beacon test call."

	def acknowledge(self) -> None:
		RecordTransaction("Beacon Notification", self.notification).retry(self._acknowledge)

	def record_status(self, status: str) -> None:
		if status not in CALL_STATUSES:
			frappe.log_error(
				f"Beacon got an unknown Twilio call status {status}",
				reference_doctype="Beacon Notification",
				reference_name=self.notification,
			)
			return

		RecordTransaction("Beacon Notification", self.notification).retry(
			lambda: self._record_status(CALL_STATUSES[status])
		)

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
			frappe.set_user(row.responder)  # nosemgrep: the verified key press acts as the called responder
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
