from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from frappe.utils import get_datetime

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_service_state.beacon_service_state import BeaconServiceState

# Best first. The status page shows the worst status of a service.
STATUS_ORDER = ("Operational", "Maintenance", "Degraded", "Partial Outage", "Major Outage")
# Below Degraded is Partial Outage, down to the Major Outage limit of the service.
STATUS_THRESHOLDS = ((99.5, "Operational"), (95.0, "Degraded"))
WORSE_STATUS_HOLD = timedelta(minutes=3)
BETTER_STATUS_HOLD = timedelta(minutes=2)


class ServiceStatus:
	"""The public status of one service in one region, damped so one bad minute does not flap it."""

	def __init__(self, state: BeaconServiceState, minute: datetime):
		self.state = state
		self.minute = minute

	def apply(self, computed: str) -> None:
		"""A worse status must hold 3 minutes and a better one 2. Maintenance applies at once."""
		if computed in (self.state.status, "Maintenance"):
			self._set(computed)
			return

		if self.state.candidate_status != computed:
			self.state.candidate_status = computed
			self.state.candidate_since = self.minute

		is_better = self.get_rank(computed) < self.get_rank(self.state.status)
		hold = BETTER_STATUS_HOLD if is_better else WORSE_STATUS_HOLD
		if self.minute - get_datetime(self.state.candidate_since) >= hold:
			self._set(computed)

	@staticmethod
	def from_ratio(ratio: float, major_outage_below: float) -> str:
		thresholds = (*STATUS_THRESHOLDS, (major_outage_below, "Partial Outage"))
		return next((status for threshold, status in thresholds if ratio >= threshold), "Major Outage")

	@staticmethod
	def with_impact(measured: str, impact: str) -> str:
		"""A published impact can only make the measured status worse. It replaces an unknown measurement."""
		if measured == "Unknown":
			return "Unknown" if impact == "Operational" else impact
		return max(measured, impact, key=STATUS_ORDER.index)

	@staticmethod
	def get_rank(status: str) -> int:
		"""Unknown ranks after every known status."""
		return STATUS_ORDER.index(status) if status in STATUS_ORDER else len(STATUS_ORDER)

	def _set(self, status: str) -> None:
		self.state.status = status
		self.state.candidate_status = None
		self.state.candidate_since = None
