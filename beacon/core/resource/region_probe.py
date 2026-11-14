from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.utils import now_datetime

from beacon.core.exceptions import ClickHouseError
from beacon.core.resource.clickhouse import ClickHouseClient
from beacon.core.transaction import RecordTransaction

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_region.beacon_region import BeaconRegion

# A state changes only after this many checks in a row agree.
CHECKS_TO_CHANGE_STATE = 2

# Under this share of synced hosts reporting to Datum, the region's telemetry is Suspect.
MIN_REPORTING_PERCENT = 50

REPORTING_QUERY = """
SELECT count() AS synced, countIf(m.last_ts > now() - INTERVAL 5 MINUTE) AS reporting
FROM (
	SELECT labels['server'] AS server
	FROM datum.latest_value(metric = 'atlas_host_sync_age_seconds', window = 900)
	WHERE value < 300
) AS a
LEFT JOIN (
	SELECT resource_id, max(ts) AS last_ts
	FROM datum.samples_recent
	WHERE metric = 'metal_build_info' AND ts > now() - INTERVAL 15 MINUTE
	GROUP BY resource_id
) AS m ON m.resource_id = a.server
"""


class RegionProbe:
	"""Checks that a region's ClickHouse answers and that its hosts still report."""

	def __init__(self, region: BeaconRegion):
		self.region = region

	def check(self) -> str:
		"""Run one check, update telemetry_state, and return it. The query runs before the region row is locked."""
		observed, reporting_percent = self._observe()
		return RecordTransaction("Beacon Region", self.region.name).retry(
			lambda: self._record(observed, reporting_percent)
		)

	def _record(self, observed: str, reporting_percent: float | None) -> str:
		"""Write only the probe columns, so a Desk edit of the region does not conflict."""
		current = frappe.db.get_value(
			"Beacon Region",
			self.region.name,
			["telemetry_state", "telemetry_streak"],
			as_dict=True,
			for_update=True,
		)
		state = current.telemetry_state
		streak = 0 if observed == state else current.telemetry_streak + 1
		if streak >= CHECKS_TO_CHANGE_STATE:
			state, streak = observed, 0

		values = {
			"telemetry_state": state,
			"telemetry_streak": streak,
			"telemetry_checked_at": now_datetime(),
			"reporting_percent": reporting_percent,
		}
		frappe.db.set_value("Beacon Region", self.region.name, values, update_modified=False)
		self.region.update(values)
		return state

	def _observe(self) -> tuple[str, float | None]:
		"""The observed state and the share of synced hosts that report."""
		try:
			row = ClickHouseClient(self.region).query(REPORTING_QUERY)[0]
		except ClickHouseError:
			# The region alert covers an outage. Log only the first failed probe of one.
			if self.region.telemetry_state != "Unreachable":
				frappe.log_error(
					"Beacon region probe failed",
					reference_doctype="Beacon Region",
					reference_name=self.region.name,
				)
			return "Unreachable", self.region.reporting_percent

		reporting_percent = 100 * row["reporting"] / row["synced"] if row["synced"] else 100
		return ("Suspect" if reporting_percent < MIN_REPORTING_PERCENT else "OK"), reporting_percent
