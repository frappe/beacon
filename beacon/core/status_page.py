from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from itertools import pairwise
from typing import Any
from xml.etree import ElementTree

import frappe
from frappe.query_builder.functions import Count, Date, Sum
from frappe.utils import add_days, get_datetime, getdate, now_datetime
from pypika.terms import Case

from beacon.core.alert.rules import BURN_KIND
from beacon.core.resource.regions import RegionSelection
from beacon.core.service.impact import CLOSED_STATUSES, PublishedImpact
from beacon.core.service.status import STATUS_ORDER, ServiceStatus

CACHE_SECONDS = 60
HISTORY_DAYS = 90
MINUTES_PER_DAY = 1440
PUBLISHED_VISIBILITIES = ("Public", "Unlisted")
# A declared service shows these minutes for each day. A Partial Outage minute is also an outage minute.
DAY_MINUTE_FIELDS = ("degraded_minutes", "outage_minutes", "partial_outage_minutes", "maintenance_minutes")
OUTAGE_STATUSES = ("Partial Outage", "Major Outage")
IMPACT_MINUTE_FIELDS = {
	"Degraded": ("degraded_minutes",),
	"Partial Outage": ("outage_minutes", "partial_outage_minutes"),
	"Major Outage": ("outage_minutes",),
	"Maintenance": ("maintenance_minutes",),
}
MEASUREMENT_MAX_AGE = timedelta(minutes=3)
PUBLIC_UPDATE_FIELDS = ["posted_at", "status", "message"]


class StatusCache:
	"""Caches each guest response for one minute. Actions clear the keys they change."""

	STATUS = "beacon:status-page:status"
	HISTORY = "beacon:status-page:history"

	@staticmethod
	def get_or_build(key: str, build: Callable[[], Any]) -> Any:
		value = frappe.cache.get_value(key)
		if value is None:
			value = build()
			frappe.cache.set_value(key, value, expires_in_sec=CACHE_SECONDS)
		return value

	@staticmethod
	def incident_key(public_id: str) -> str:
		return f"beacon:status-page:incident:{public_id}"

	@staticmethod
	def clear(public_id: str) -> None:
		for key in (StatusCache.STATUS, StatusCache.HISTORY, StatusCache.incident_key(public_id)):
			frappe.cache.delete_value(key)


class StatusPage:
	"""Builds the public data of the status page. Only published incidents appear."""

	def get_status(self) -> dict:
		settings = frappe.get_cached_doc("Beacon Settings")
		incidents = self._get_open_published_incidents()
		impacts = PublishedImpact()
		days = self._get_days()
		visible_services = self._get_services()
		declared_services = [service for service in visible_services if service.measurement == "Declared"]
		self.declared_days = self._get_declared_days(declared_services) if declared_services else {}
		self.states = self._get_states()

		regions = []
		for region in frappe.get_all(
			"Beacon Region",
			filters={"status": "Active", "show_on_status_page": 1},
			fields=["name", "title"],
			order_by="sort_order asc, title asc",
		):
			services = [
				self._get_service(service, region.name, impacts, days)
				for service in visible_services
				if service.scope == "Region" and region.name in service.region_names
			]
			if services:
				regions.append(
					{
						"region": region.name,
						"title": region.title,
						"status": self._get_worst(services),
						"services": services,
					}
				)

		global_services = [
			self._get_service(service, service.query_region, impacts, days)
			for service in visible_services
			if service.scope == "Global"
		]
		return {
			"title": settings.status_page_title,
			"updated_at": self._to_utc_iso(now_datetime()),
			"status": self._get_worst([*regions, *global_services]),
			"regions": regions,
			"global_services": global_services,
			"incidents": [self._summarize(incident) for incident in incidents if incident.type == "Incident"],
			"maintenances": [
				self._summarize(incident) for incident in incidents if incident.type == "Maintenance"
			],
		}

	def get_history(self) -> dict:
		incidents = frappe.get_all(
			"Beacon Incident",
			filters={
				"visibility": "Public",
				"status": ("in", ("Resolved", "Completed")),
				"resolved_at": (">", add_days(now_datetime(), -HISTORY_DAYS)),
			},
			fields=[
				"name",
				"public_id",
				"title",
				"type",
				"status",
				"severity",
				"creation",
				"resolved_at",
				"scheduled_start",
				"scheduled_end",
			],
			order_by="resolved_at desc",
		)
		return {
			"title": frappe.db.get_single_value("Beacon Settings", "status_page_title"),
			"updated_at": self._to_utc_iso(now_datetime()),
			"incidents": [self._summarize(item) for item in incidents],
		}

	def get_incident(self, public_id: str) -> dict | None:
		name = self.find_incident(public_id)
		return self._get_detail(name) if name else None

	def find_incident(self, public_id: str) -> str | None:
		"""The published incident of a link. The link of a merged incident opens the incident that it was
		merged into, unless that one is Internal."""
		incident = frappe.db.get_value(
			"Beacon Incident",
			{"public_id": public_id, "visibility": ("in", PUBLISHED_VISIBILITIES)},
			["name", "status", "merged_into"],
			as_dict=True,
		)
		if incident and incident.status == "Merged" and incident.merged_into:
			target = frappe.db.get_value(
				"Beacon Incident",
				frappe.get_doc("Beacon Incident", incident.name).get_final_incident(),
				["name", "visibility"],
				as_dict=True,
			)
			incident = target if target and target.visibility != "Internal" else None
		return incident.name if incident else None

	def _get_detail(self, name: str) -> dict:
		incident = frappe.get_doc("Beacon Incident", name)
		detail = self._summarize(incident)
		detail["site_title"] = frappe.db.get_single_value("Beacon Settings", "status_page_title")
		detail["updates"] = [
			{
				field: self._to_utc_iso(row.posted_at) if field == "posted_at" else row.get(field)
				for field in PUBLIC_UPDATE_FIELDS
			}
			for row in reversed(incident.updates)
			if row.is_public
		]
		titles = dict(
			frappe.get_all(
				"Beacon Alert Rule",
				filters={
					"kind": BURN_KIND,
					"name": ("in", [row.service for row in incident.affected_services]),
				},
				fields=["name", "title"],
				as_list=True,
			)
		)
		detail["affected_services"] = [
			{
				"service": row.service,
				"title": titles.get(row.service),
				"region": row.region,
				"impact": row.impact,
			}
			for row in incident.affected_services
		]
		detail["public_rca"] = incident.public_rca if incident.status in CLOSED_STATUSES else None
		return detail

	def _summarize(self, incident: frappe._dict) -> dict:
		return {
			"id": incident.public_id,
			"title": incident.title,
			"type": incident.type,
			"status": incident.status,
			"started_at": self._to_utc_iso(incident.creation),
			"resolved_at": self._to_utc_iso(incident.resolved_at),
			"scheduled_start": self._to_utc_iso(incident.scheduled_start),
			"scheduled_end": self._to_utc_iso(incident.scheduled_end),
		}

	def _get_open_published_incidents(self) -> list[frappe._dict]:
		return frappe.get_all(
			"Beacon Incident",
			filters={"visibility": "Public", "status": ("not in", CLOSED_STATUSES)},
			fields=[
				"name",
				"public_id",
				"title",
				"type",
				"status",
				"creation",
				"resolved_at",
				"scheduled_start",
				"scheduled_end",
			],
			order_by="creation desc",
		)

	def _get_services(self) -> list[frappe._dict]:
		services = frappe.get_all(
			"Beacon Alert Rule",
			filters={"kind": BURN_KIND, "show_on_status_page": 1, "enabled": 1},
			fields=[
				"name",
				"title",
				"description",
				"scope",
				"query_region",
				"measurement",
				"status_page_group",
				"creation",
			],
			order_by="sort_order asc, title asc",
		)
		selection = RegionSelection("Beacon Alert Rule")
		for service in services:
			service.region_names = selection.chosen.get(service.name) or selection.active
		return services

	def _get_states(self) -> dict[tuple[str, str], frappe._dict]:
		return {
			(state.service, state.region): state
			for state in frappe.get_all(
				"Beacon Service State",
				fields=["service", "region", "status", "checked_at", "last_counted_minute"],
			)
		}

	def _get_days(self) -> dict[tuple[str, str], list[frappe._dict]]:
		"""The minute totals of each service in each region for each UTC day of the history."""
		minute = frappe.qb.DocType("Beacon Service Minute")
		utc_day = Date(minute.minute)
		rows = (
			frappe.qb.from_(minute)
			.select(
				minute.service,
				minute.region,
				utc_day.as_("date"),
				Sum(minute.good).as_("good"),
				Sum(minute.valid).as_("valid"),
				Count(minute.name).as_("observed_minutes"),
				Sum(Case().when(minute.status == "Degraded", 1).else_(0)).as_("degraded_minutes"),
				Sum(Case().when(minute.status.isin(OUTAGE_STATUSES), 1).else_(0)).as_("outage_minutes"),
				Sum(Case().when(minute.status == "Partial Outage", 1).else_(0)).as_("partial_outage_minutes"),
				Sum(Case().when(minute.status == "Maintenance", 1).else_(0)).as_("maintenance_minutes"),
			)
			.where(
				(minute.minute >= get_datetime(add_days(getdate(), -HISTORY_DAYS + 1)))
				& (minute.minute < get_datetime(add_days(getdate(), 1)))
			)
			.groupby(minute.service, minute.region, utc_day)
			.orderby(utc_day)
		).run(as_dict=True)
		days: dict[tuple[str, str], list[frappe._dict]] = {}
		for row in rows:
			day = frappe._dict(row)
			for field in (
				"good",
				"valid",
				"observed_minutes",
				"degraded_minutes",
				"outage_minutes",
				"partial_outage_minutes",
				"maintenance_minutes",
			):
				day[field] = int(day[field] or 0)
			days.setdefault((day.service, day.region), []).append(day)
		return days

	def _get_service(self, service: frappe._dict, region: str, impacts: PublishedImpact, days: dict) -> dict:
		impact = impacts.get(service.name, region)
		row = {
			"service": service.name,
			"title": service.title,
			"description": service.description,
			"group": service.status_page_group or None,
			"measurement": service.measurement,
			"declared_impact": impact,
			"history_days": HISTORY_DAYS,
		}
		if service.measurement == "Declared":
			return {**row, **self._get_declared_service(service, region, impact)}
		return {**row, **self._get_measured_service(service.name, region, impact, days)}

	def _get_declared_service(self, service: frappe._dict, region: str, impact: str) -> dict:
		"""No SLI: the status is the impact of open published incidents, and each day shows their minutes."""
		history = self.declared_days.get((service.name, region), {})
		return {
			"status": impact,
			"measurement_status": "Declared",
			"stale": False,
			"checked_at": None,
			"last_counted_minute": None,
			"coverage": None,
			"uptime": None,
			"declared_since": str(getdate(service.creation)),
			"days": [{"date": date, **minutes} for date, minutes in sorted(history.items())],
		}

	def _get_measured_service(self, service: str, region: str, impact: str, days: dict) -> dict:
		state = self.states.get((service, region)) or frappe._dict(status="Unknown")
		age = now_datetime() - get_datetime(state.checked_at) if state.checked_at else None
		stale = age is None or age < timedelta(0) or age > MEASUREMENT_MAX_AGE
		measured_status = "Unknown" if stale else state.status
		status = ServiceStatus.with_impact(measured_status, impact)
		history = days.get((service, region), [])
		good, valid = sum(day.good for day in history), sum(day.valid for day in history)
		return {
			"status": status,
			"measurement_status": measured_status,
			"stale": stale,
			"checked_at": self._to_utc_iso(state.checked_at),
			"last_counted_minute": self._to_utc_iso(state.last_counted_minute),
			"coverage": round(
				sum(day.observed_minutes for day in history) / (HISTORY_DAYS * MINUTES_PER_DAY), 4
			),
			"uptime": round(100 * good / valid, 3) if valid else None,
			"days": [
				{
					"date": str(day.date),
					"uptime": round(100 * day.good / day.valid, 3) if day.valid else None,
					"coverage": round(day.observed_minutes / MINUTES_PER_DAY, 3),
					"good": day.good,
					"valid": day.valid,
					"observed_minutes": day.observed_minutes,
					"degraded_minutes": day.degraded_minutes,
					"outage_minutes": day.outage_minutes,
					"partial_outage_minutes": day.partial_outage_minutes,
					"maintenance_minutes": day.maintenance_minutes,
				}
				for day in history
			],
		}

	def _get_declared_days(
		self, services: list[frappe._dict]
	) -> dict[tuple[str, str], dict[str, dict[str, int]]]:
		"""For each (service, region), the minutes of each published impact on each UTC day of the history.
		A blank region means every region. An open incident counts until now. Maintenance counts from its
		scheduled start."""
		intervals = self._get_declared_intervals()
		days: dict[tuple[str, str], dict[str, dict[str, int]]] = {}
		for service in services:
			for region in [service.query_region] if service.scope == "Global" else service.region_names:
				items = [
					(begin, end, impact)
					for begin, end, item_region, impact in intervals.get(service.name, [])
					if item_region in ("", region)
				]
				if items:
					days[(service.name, region)] = _sum_impact_days(items)
		return days

	@staticmethod
	def _get_declared_intervals() -> dict[str, list[tuple[datetime, datetime, str, str]]]:
		now = now_datetime()
		start = get_datetime(add_days(getdate(), -HISTORY_DAYS + 1))
		incidents = {
			incident.name: incident
			for incident in frappe.get_all(
				"Beacon Incident",
				filters={"visibility": "Public", "status": ("not in", ("Merged", "Cancelled", "Scheduled"))},
				or_filters=[["resolved_at", "is", "not set"], ["resolved_at", ">=", start]],
				fields=["name", "creation", "scheduled_start", "resolved_at"],
			)
		}
		rows = frappe.get_all(
			"Beacon Incident Service",
			filters={"parent": ("in", list(incidents) or [""])},
			fields=["parent", "service", "region", "impact"],
		)
		intervals: dict[str, list[tuple[datetime, datetime, str, str]]] = {}
		for row in rows:
			incident = incidents[row.parent]
			begin = max(get_datetime(incident.scheduled_start or incident.creation), start)
			end = get_datetime(incident.resolved_at) if incident.resolved_at else now
			if begin < end:
				intervals.setdefault(row.service, []).append((begin, end, row.region or "", row.impact))
		return intervals

	@staticmethod
	def _to_utc_iso(value: str | datetime | None) -> str | None:
		if not value:
			return None

		date = get_datetime(value)
		return (date.replace(tzinfo=UTC) if date.tzinfo is None else date.astimezone(UTC)).isoformat()

	@staticmethod
	def _get_worst(items: list[dict]) -> str:
		"""The worst status of the items. Unknown counts only when nothing else is known."""
		statuses = [item["status"] for item in items if item["status"] != "Unknown"]
		return max(statuses, key=STATUS_ORDER.index) if statuses else "Unknown"


def _sum_impact_days(intervals: list[tuple[datetime, datetime, str]]) -> dict[str, dict[str, int]]:
	"""Split the intervals at UTC midnight, then count each minute once at its worst impact."""
	segments: dict[str, list[tuple[datetime, datetime, str]]] = {}
	for begin, end, impact in intervals:
		while begin < end:
			day_end = min(get_datetime(add_days(begin.date(), 1)), end)
			segments.setdefault(str(begin.date()), []).append((begin, day_end, impact))
			begin = day_end
	return {day: _sum_impact_day(items) for day, items in segments.items()}


def _sum_impact_day(segments: list[tuple[datetime, datetime, str]]) -> dict[str, int]:
	points = sorted({point for begin, end, _ in segments for point in (begin, end)})
	seconds = dict.fromkeys(DAY_MINUTE_FIELDS, 0.0)
	for begin, end in pairwise(points):
		active = [
			impact for item_begin, item_end, impact in segments if item_begin <= begin and end <= item_end
		]
		if not active:
			continue
		duration = (end - begin).total_seconds()
		for field in IMPACT_MINUTE_FIELDS[max(active, key=STATUS_ORDER.index)]:
			seconds[field] += duration
	return {field: min(int(total // 60), MINUTES_PER_DAY) for field, total in seconds.items()}


class IncidentFeed:
	"""The public updates of one published incident as an RSS 2.0 feed, newest first."""

	def __init__(self, incident: dict, link: str):
		self.incident = incident
		self.link = link

	def build(self) -> bytes:
		feed = ElementTree.Element("rss", version="2.0")
		channel = ElementTree.SubElement(feed, "channel")
		self._add(channel, title=self.incident["title"], link=self.link, description=self.incident["status"])
		for update in self.incident["updates"]:
			posted_at = get_datetime(update["posted_at"])
			item = ElementTree.SubElement(channel, "item")
			self._add(
				item,
				title=f"{update['status']}: {self.incident['title']}",
				link=self.link,
				description=update["message"],
				guid=f"{self.incident['id']}:{update['posted_at']}",
				pubDate=format_datetime(posted_at.replace(tzinfo=posted_at.tzinfo or UTC)),
			)
			item.find("guid").set("isPermaLink", "false")
		return ElementTree.tostring(feed, xml_declaration=True, encoding="utf-8")

	@staticmethod
	def _add(parent: ElementTree.Element, **children: str) -> None:
		for tag, text in children.items():
			ElementTree.SubElement(parent, tag).text = text
