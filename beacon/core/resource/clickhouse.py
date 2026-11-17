from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import frappe
import requests

from beacon.core.exceptions import ClickHouseError

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_region.beacon_region import BeaconRegion

LIMIT_SETTINGS_QUERY = """
SELECT name, value FROM system.settings
WHERE name IN ('max_execution_time', 'max_memory_usage', 'max_threads', 'readonly')
"""

# The beacon user's profile stops a query after 20 seconds. The HTTP timeout leaves room for that answer.
QUERY_TIMEOUT_SECONDS = 25


class ClickHouseClient:
	"""Read-only access to one region's ClickHouse over HTTP."""

	def __init__(self, region: BeaconRegion):
		self.region = region.name
		self.url = region.clickhouse_url
		self.database = region.clickhouse_database
		self.auth = (
			region.clickhouse_user,
			region.get_password("clickhouse_password", raise_exception=False) or "",
		)
		# A query that adds many resources into one row cannot leave them out after it runs, so it filters on this list.
		excluded = frappe.get_all(
			"Beacon Resource", filters={"region": self.region, "monitoring_disabled": 1}, pluck="resource_id"
		)
		self.excluded = self.quote_array(excluded)

	@staticmethod
	def quote_array(values: list[str]) -> str:
		escaped = (str(value).replace("\\", "\\\\").replace("'", "\\'") for value in values)
		return "[" + ",".join(f"'{value}'" for value in escaped) + "]"

	def query(self, sql: str, **parameters: Any) -> list[dict[str, Any]]:
		"""Run one SELECT and return its rows. `{region:String}` and `{excluded:Array(String)}`, the resources
		with monitoring disabled, are always available to the query."""
		parameters = {"region": self.region, "excluded": self.excluded, **parameters}
		params = {
			"database": self.database,
			"default_format": "JSONEachRow",
			"output_format_json_quote_64bit_integers": 0,
			**{f"param_{name}": value for name, value in parameters.items()},
		}

		try:
			response = requests.post(
				self.url, params=params, data=sql.encode(), auth=self.auth, timeout=QUERY_TIMEOUT_SECONDS
			)
		except requests.RequestException as error:
			raise ClickHouseError(f"{self.region}: {error}") from error

		if response.status_code != 200:
			raise ClickHouseError(f"{self.region}: HTTP {response.status_code}: {response.text[:2000]}")

		return [json.loads(line) for line in response.text.splitlines() if line]

	def ping(self) -> float:
		"""Run SELECT 1 and return the latency in milliseconds."""
		started = time.monotonic()
		self.query("SELECT 1 AS ok")
		return (time.monotonic() - started) * 1000

	def get_limit_settings(self) -> dict[str, str]:
		"""The profile limits of the beacon user, to check that Beacon cannot write or run heavy queries."""
		rows = self.query(LIMIT_SETTINGS_QUERY)
		return {row["name"]: row["value"] for row in rows}
