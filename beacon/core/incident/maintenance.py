from __future__ import annotations

from datetime import datetime

import frappe
from frappe.utils import now_datetime

from beacon.core.transaction import RecordTransaction


class MaintenanceSchedule:
	"""Starts and completes maintenance windows at their scheduled times."""

	def advance(self) -> None:
		now = now_datetime()
		self._move("Scheduled", "scheduled_start", now, "start_maintenance")
		self._move("In Progress", "scheduled_end", now, "complete_maintenance")

	def _move(self, status: str, time_field: str, now: datetime, action: str) -> None:
		names = frappe.get_all(
			"Beacon Incident",
			filters={"type": "Maintenance", "status": status, time_field: ("<=", now)},
			pluck="name",
		)

		def move(name: str) -> None:
			maintenance = frappe.get_doc("Beacon Incident", name, for_update=True)
			if maintenance.status == status:
				getattr(maintenance, action)()

		RecordTransaction.run_each(
			"Beacon Incident", names, f"Beacon could not {action.replace('_', ' ')}", move
		)
