from __future__ import annotations

from datetime import datetime

import frappe
from frappe import _
from frappe.utils import get_datetime

from beacon.core.exceptions import ClickHouseError
from beacon.core.resource.charts import get_time_window, to_iso_utc, to_utc_naive
from beacon.core.resource.clickhouse import ClickHouseClient

LEVELS = ("debug", "info", "notice", "warning", "error", "critical", "alert", "emergency")
PAGE_LINES = 500
MAX_PATTERNS = 500
MAX_SERVICES = 200
PATTERN_SQL = (
	"replaceRegexpAll("
	"replaceRegexpAll("
	"replaceRegexpAll(message, "
	"'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', '<id>'), "
	"'[0-9a-fA-F]{8,}', '<id>'), "
	"'[0-9]+', '<n>')"
)
SEARCH_SQL = (
	"(positionCaseInsensitive(message, {search:String}) > 0"
	" OR arrayExists(v -> positionCaseInsensitive(v, {search:String}) > 0, mapValues(attributes)))"
)


class LogSearch:
	"""The log histogram, level counts and lines or groups of one region, or of one resource in it."""

	def __init__(self, region: str, resource_id: str | None = None):
		self.region = region
		self.resource_id = resource_id

	def get_logs(
		self,
		start: str,
		end: str,
		levels: str | None = None,
		service: str | None = None,
		search: str | None = None,
		before: str | None = None,
		mode: str = "lines",
	) -> dict:
		start_time, end_time, step = get_time_window(start, end)
		levels = self._get_levels(levels)
		service = self._get_service(service)
		search = self._get_search(search)
		before_time = self._get_before(before)
		if mode not in ("lines", "groups"):
			frappe.throw(_("Choose lines or groups."))

		self.clickhouse = ClickHouseClient(frappe.get_doc("Beacon Region", self.region))
		where, parameters = self._where(start_time, end_time, levels=levels, service=service, search=search)
		try:
			histogram = self._get_histogram(where, parameters, step)
			return {
				"histogram": histogram,
				"levels": self._get_level_counts(start_time, end_time, service, search),
				"services": self._get_services(start_time, end_time),
				**(
					{
						"groups": self._get_groups(
							where, parameters, step, sorted({row["ts"] for row in histogram})
						)
					}
					if mode == "groups"
					else self._get_lines(where, parameters, before_time)
				),
			}
		except ClickHouseError as error:
			frappe.throw(_("Cannot read logs in {0}: {1}").format(self.region, str(error)))

	def _get_histogram(self, where, parameters, step) -> list[dict]:
		sql = (
			"SELECT toStartOfInterval(ts, toIntervalSecond({step:UInt32})) AS bucket, level, count() AS count "
			"FROM logs WHERE " + where + " GROUP BY bucket, level ORDER BY bucket, level"
		)
		rows = self.clickhouse.query(sql, step=step, **parameters)
		return [
			{"ts": to_iso_utc(row["bucket"]), "level": row["level"], "count": row["count"]} for row in rows
		]

	def _get_level_counts(self, start, end, service, search) -> dict[str, int]:
		where, parameters = self._where(start, end, service=service, search=search)
		sql = "SELECT level, count() AS count FROM logs WHERE " + where + " GROUP BY level"
		rows = self.clickhouse.query(sql, **parameters)
		return {row["level"]: row["count"] for row in rows}

	def _get_services(self, start, end) -> list[str]:
		where, parameters = self._where(start, end)
		sql = (
			"SELECT DISTINCT service FROM logs WHERE "
			+ where
			+ f" AND service != '' ORDER BY service LIMIT {MAX_SERVICES}"
		)
		rows = self.clickhouse.query(sql, **parameters)
		return [row["service"] for row in rows]

	def _get_lines(self, where, parameters, before) -> dict:
		if before:
			where += " AND ts < {before:DateTime64(3)}"
			parameters = {**parameters, "before": self._to_sql(before)}
		sql = (
			"SELECT ts, resource_id, level, service, source, message, attributes FROM logs WHERE "
			+ where
			+ f" ORDER BY ts DESC LIMIT {PAGE_LINES}"
		)
		rows = self.clickhouse.query(sql, **parameters)
		resources = self._get_resources({row["resource_id"] for row in rows})
		lines = [
			{
				"ts": to_iso_utc(row["ts"]),
				**resources.get(row["resource_id"], {"resource": None, "resource_title": row["resource_id"]}),
				"level": row["level"],
				"service": row["service"],
				"source": row["source"],
				"message": row["message"],
				"attributes": row["attributes"],
			}
			for row in rows
		]
		return {"lines": lines, "next_before": lines[-1]["ts"] if len(lines) == PAGE_LINES else None}

	def _get_resources(self, resource_ids: set[str]) -> dict[str, dict]:
		"""The Desk name and title of each resource that wrote a line, so a line links to its resource."""
		rows = frappe.get_all(
			"Beacon Resource",
			filters={"region": self.region, "resource_id": ("in", list(resource_ids))},
			fields=["name", "resource_id", "title"],
		)
		return {
			row.resource_id: {"resource": row.name, "resource_title": row.title or row.resource_id}
			for row in rows
		}

	def _get_groups(self, where, parameters, step, buckets) -> list[dict]:
		source = f"(SELECT {PATTERN_SQL} AS pattern, ts, level, message FROM logs WHERE {where})"
		sql = (
			"SELECT pattern, count() AS count, min(ts) AS first_seen, max(ts) AS last_seen, "
			f"any(message) AS sample, max(indexOf({{level_order:Array(String)}}, lower(level))) AS level_rank "
			f"FROM {source} GROUP BY pattern ORDER BY count DESC LIMIT {MAX_PATTERNS}"
		)
		rows = self.clickhouse.query(
			sql, level_order=ClickHouseClient.quote_array(list(LEVELS)), **parameters
		)
		if not rows:
			return []

		patterns = [row["pattern"] for row in rows]
		trend_sql = (
			f"SELECT pattern, toStartOfInterval(ts, toIntervalSecond({{step:UInt32}})) AS bucket, count() AS count "
			f"FROM {source} WHERE pattern IN {{patterns:Array(String)}} GROUP BY pattern, bucket"
		)
		trend_rows = self.clickhouse.query(
			trend_sql, step=step, patterns=ClickHouseClient.quote_array(patterns), **parameters
		)
		return self._build_groups(rows, trend_rows, buckets)

	@staticmethod
	def _build_groups(rows: list[dict], trend_rows: list[dict], buckets: list[str]) -> list[dict]:
		trends: dict[str, dict[str, int]] = {}
		for row in trend_rows:
			trends.setdefault(row["pattern"], {})[to_iso_utc(row["bucket"])] = row["count"]
		return [
			{
				"pattern": row["pattern"],
				"level": LEVELS[row["level_rank"] - 1] if row["level_rank"] else "info",
				"count": row["count"],
				"first_seen": to_iso_utc(row["first_seen"]),
				"last_seen": to_iso_utc(row["last_seen"]),
				"sample": row["sample"],
				"trend": [trends.get(row["pattern"], {}).get(bucket, 0) for bucket in buckets],
			}
			for row in rows
		]

	def _where(
		self,
		start: datetime,
		end: datetime,
		levels: list[str] | None = None,
		service: str | None = None,
		search: str | None = None,
	) -> tuple[str, dict]:
		conditions = ["ts >= {start:DateTime64(3)}", "ts < {end:DateTime64(3)}"]
		parameters = {"start": self._to_sql(start), "end": self._to_sql(end)}
		if self.resource_id:
			conditions.append("resource_id = {resource_id:String}")
			parameters["resource_id"] = self.resource_id
		if levels:
			conditions.append("level IN {levels:Array(String)}")
			parameters["levels"] = ClickHouseClient.quote_array(levels)
		if service:
			conditions.append("service = {service:String}")
			parameters["service"] = service
		if search:
			conditions.append(SEARCH_SQL)
			parameters["search"] = search
		return " AND ".join(conditions), parameters

	@staticmethod
	def _get_levels(levels) -> list[str] | None:
		names = [name.strip().lower() for name in (levels or "").split(",") if name.strip()]
		if any(name not in LEVELS for name in names):
			frappe.throw(_("Choose levels from {0}.").format(", ".join(LEVELS)))
		return names or None

	@staticmethod
	def _get_service(service) -> str | None:
		service = (service or "").strip()
		if len(service) > 100:
			frappe.throw(_("The service is too long."))
		return service or None

	@staticmethod
	def _get_search(search) -> str | None:
		search = (search or "").strip()
		if len(search) > 200:
			frappe.throw(_("The search text is too long."))
		return search or None

	@staticmethod
	def _get_before(before) -> datetime | None:
		if not before:
			return None
		before_time = get_datetime(before)
		if not before_time:
			frappe.throw(_("Choose a valid time."))
		return to_utc_naive(before_time)

	@staticmethod
	def _to_sql(value: datetime) -> str:
		return to_utc_naive(value).strftime("%Y-%m-%d %H:%M:%S")
