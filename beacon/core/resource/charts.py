from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.utils import get_datetime

from beacon.core.alert.rules import AlertRules
from beacon.core.exceptions import ClickHouseError, RuleContractError
from beacon.core.resource.clickhouse import ClickHouseClient

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_resource.beacon_resource import BeaconResource

MAX_BUCKETS = 600
MAX_SERIES = 50
# Each series has at most MAX_BUCKETS buckets, plus one partial bucket.
MAX_POINTS = 50_000
# Datum keeps the raw system_ samples for 15 days.
MAX_WINDOW_SECONDS = 14 * 24 * 60 * 60
CHART_COLUMNS = ("ts", "series", "value")


def get_time_window(start: str, end: str) -> tuple[datetime, datetime, int]:
	"""Round the window to the minute and return the bucket size: whole minutes, and at most MAX_BUCKETS buckets."""
	start_time = to_utc_naive(start).replace(second=0, microsecond=0)
	end_time = to_utc_naive(end).replace(second=0, microsecond=0)
	if end_time <= start_time:
		frappe.throw(_("The end must be after the start."))
	span = int((end_time - start_time).total_seconds())
	if span > MAX_WINDOW_SECONDS:
		frappe.throw(_("Choose a window of at most 14 days."))
	return start_time, end_time, 60 * max(1, -(-span // (MAX_BUCKETS * 60)))


def to_utc_naive(value) -> datetime:
	moment = get_datetime(value)
	if moment.tzinfo:
		return moment.astimezone(UTC).replace(tzinfo=None)
	return moment


def to_iso_utc(value) -> str:
	return to_utc_naive(value).replace(tzinfo=UTC).isoformat()


class ChartQuery:
	"""Runs a chart query and checks its result contract."""

	def __init__(self, clickhouse: ClickHouseClient, query: str):
		self.clickhouse = clickhouse
		self.query = query

	def run(self, resource_id: str, start: datetime, end: datetime, step: int) -> list[dict]:
		rows = self.clickhouse.query(
			self.query,
			resource_id=resource_id,
			start=to_utc_naive(start).strftime("%Y-%m-%d %H:%M:%S"),
			end=to_utc_naive(end).strftime("%Y-%m-%d %H:%M:%S"),
			step=step,
		)
		return self._check_rows(rows)

	@staticmethod
	def _check_rows(rows: list[dict]) -> list[dict]:
		if len(rows) > MAX_POINTS:
			raise RuleContractError(f"The query returned {len(rows)} rows. The limit is {MAX_POINTS}.")
		for row in rows:
			if set(row) != set(CHART_COLUMNS):
				raise RuleContractError(
					f"The query must return {', '.join(CHART_COLUMNS)}. It returned {', '.join(row)}."
				)
		series = {row["series"] for row in rows}
		if len(series) > MAX_SERIES:
			raise RuleContractError(f"The query returned {len(series)} series. The limit is {MAX_SERIES}.")
		return [
			{
				"ts": to_iso_utc(row["ts"]),
				"series": str(row["series"]),
				"value": None if row["value"] is None else float(row["value"]),
			}
			for row in rows
		]


class ResourceCharts:
	"""The chart definitions, alerts and chart data of one resource."""

	def __init__(self, resource: BeaconResource):
		self.resource = resource

	def get_definitions(self, start: datetime, end: datetime) -> list[dict]:
		"""The charts of the resource type. A chart with a required metric shows only when the resource sent that metric in the window, because one type can have many roles."""
		charts = [
			chart
			for chart in frappe.get_all(
				"Beacon Chart",
				fields=[
					"name",
					"title",
					"resource_type",
					"requires_metric",
					"section",
					"unit",
					"style",
					"is_key_chart",
				],
				order_by="section asc, sort_order asc, title asc",
			)
			if self._matches(chart.resource_type)
		]
		required = {chart.requires_metric for chart in charts if chart.requires_metric}
		sent = self._get_sent_metrics(required, start, end) if required else set()
		return [
			{key: value for key, value in chart.items() if key not in ("resource_type", "requires_metric")}
			for chart in charts
			if not chart.requires_metric or chart.requires_metric in sent
		]

	def get_alerts(self, start: datetime, end: datetime) -> list[dict]:
		return frappe.get_all(
			"Beacon Alert",
			filters={"resource": self.resource.name, "started_at": ("<", end)},
			or_filters=[["resolved_at", "is", "not set"], ["resolved_at", ">", start]],
			fields=[
				"name",
				"title",
				"severity",
				"status",
				"started_at",
				"fired_at",
				"resolved_at",
				"incident",
			],
			order_by="started_at desc",
			limit=50,
		)

	def get_chart(self, chart: str, start: str, end: str) -> dict:
		start_time, end_time, step = get_time_window(start, end)
		definition = frappe.get_doc("Beacon Chart", chart)
		if not self._matches(definition.resource_type):
			frappe.throw(
				_("The chart {0} does not apply to this resource.").format(definition.title),
				frappe.PermissionError,
			)

		key = f"beacon:chart:{definition.name}:{self.resource.name}:{start_time}:{end_time}"
		cached = frappe.cache.get_value(key)
		if cached is not None:
			return cached

		region = frappe.get_doc("Beacon Region", self.resource.region)
		try:
			rows = ChartQuery(ClickHouseClient(region), definition.query).run(
				self.resource.resource_id, start_time, end_time, step
			)
		except ClickHouseError as error:
			frappe.throw(_("Cannot read {0}: {1}").format(self.resource.region, str(error)))

		result = self._build(definition, step, rows)
		frappe.cache.set_value(key, result, expires_in_sec=60)
		return result

	def _get_sent_metrics(self, metrics: set[str], start: datetime, end: datetime) -> set[str]:
		key = f"beacon:sent-metrics:{self.resource.name}:{start}:{end}:{','.join(sorted(metrics))}"
		cached = frappe.cache.get_value(key)
		if cached is not None:
			return set(cached)

		client = ClickHouseClient(frappe.get_doc("Beacon Region", self.resource.region))
		try:
			rows = client.query(
				"SELECT DISTINCT metric FROM datum.samples"
				" WHERE resource_id = {resource_id:String} AND has({metrics:Array(String)}, metric)"
				" AND ts >= {start:DateTime64(3)} AND ts < {end:DateTime64(3)}",
				resource_id=self.resource.resource_id,
				metrics=client.quote_array(sorted(metrics)),
				start=start.strftime("%Y-%m-%d %H:%M:%S"),
				end=end.strftime("%Y-%m-%d %H:%M:%S"),
			)
		except ClickHouseError as error:
			frappe.throw(_("Cannot read {0}: {1}").format(self.resource.region, str(error)))

		sent = {row["metric"] for row in rows}
		frappe.cache.set_value(key, sorted(sent), expires_in_sec=60)
		return sent

	def _matches(self, resource_type: str | None) -> bool:
		types = AlertRules.get_resource_types(resource_type)
		return not types or self.resource.resource_type in types

	@staticmethod
	def _build(definition, step: int, rows: list[dict]) -> dict:
		series: dict[str, list] = {}
		for row in rows:
			series.setdefault(row["series"], []).append([row["ts"], row["value"]])
		return {
			"title": definition.title,
			"unit": definition.unit,
			"style": definition.style,
			"step": step,
			"series": [{"name": name, "points": points} for name, points in series.items()],
		}
