from __future__ import annotations

import frappe

from beacon.core.alert.states import OPEN_ALERT_STATUSES
from beacon.core.incident.chat_message import ChatMessage
from beacon.core.incident.outbox import NotificationOutbox
from beacon.core.transaction import RecordTransaction


class IncidentRecovery:
	"""Closes incidents whose alerts all recovered. Only Internal Beacon incidents close without a person.

	Callers change alerts and queue this after their commit. So it never holds alert locks while it locks an
	incident, and every incident is locked before its alerts.
	"""

	@staticmethod
	def queue(incidents: set[str]) -> None:
		if not incidents:
			return
		from beacon.core.jobs import queue_incident_recovery

		queue_incident_recovery(sorted(incidents))

	def resolve_recovered(self, incidents: list[str]) -> None:
		RecordTransaction.run_each(
			"Beacon Incident", incidents, "Beacon could not close a recovered incident", self._resolve
		)

	def _resolve(self, name: str) -> None:
		incident = frappe.get_doc("Beacon Incident", name, for_update=True)
		if not incident.lifecycle.is_open:
			return
		if frappe.db.exists("Beacon Alert", {"incident": name, "status": ("in", OPEN_ALERT_STATUSES)}):
			return

		if incident.visibility == "Internal" and incident.created_by_beacon:
			incident.resolve("All alerts recovered.")
		else:
			NotificationOutbox().announce(
				incident,
				f"⏸️ **No open alerts remain** on {ChatMessage.link(name)}. Check the service, then resolve it.",
			)
