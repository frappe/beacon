from datetime import timedelta

import frappe
import requests
from frappe.utils import now_datetime

from beacon.core.alert.rule_evaluator import RegionEvaluation
from beacon.core.resource.regions import RegionSelection

HEARTBEAT_TIMEOUT_SECONDS = 10

# Work this late means that the workers do not keep up, even when the scheduler runs.
MAXIMUM_DELAY = timedelta(minutes=5)


class Heartbeat:
	"""Reports to the configured external endpoint that the scheduler runs and the workers keep up."""

	def send(self) -> None:
		url = frappe.get_cached_doc("Beacon Settings").get_password("heartbeat_url", raise_exception=False)
		if not url:
			return
		if late := self._get_late_work():
			frappe.logger("beacon").warning(f"Beacon held the heartbeat: {late} are late")
			return

		try:
			requests.get(url, timeout=HEARTBEAT_TIMEOUT_SECONDS)
		except requests.RequestException:
			frappe.log_error("Beacon heartbeat failed")

	def _get_late_work(self) -> str | None:
		cutoff = now_datetime() - MAXIMUM_DELAY
		if any(
			RegionEvaluation.get_due_evaluations(region, cutoff) for region in RegionSelection.get_active()
		):
			return "rule evaluations"
		if frappe.db.exists("Beacon Notification", {"status": "Queued", "creation": ("<", cutoff)}):
			return "notifications"
