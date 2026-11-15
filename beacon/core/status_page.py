from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from xml.etree import ElementTree

import frappe
from frappe.utils import add_days, get_datetime, getdate, now_datetime

from beacon.core.incident.merge import IncidentMerge
from beacon.core.resource.regions import RegionSelection
from beacon.core.service.status import STATUS_ORDER

CACHE_SECONDS = 60
HISTORY_DAYS = 90
MINUTES_PER_DAY = 1440
CLOSED_STATUSES = ("Resolved", "Merged", "Completed", "Cancelled")
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
	def incident_key(name: str) -> str:
		return f"beacon:status-page:incident:{name}"

	@staticmethod
	def unlisted_key(token: str) -> str:
		return f"beacon:status-page:unlisted:{token}"

	@staticmethod
	def clear(incident: str, share_token: str | None = None) -> None:
		keys = [StatusCache.STATUS, StatusCache.HISTORY, StatusCache.incident_key(incident)]
		if share_token:
			keys.append(StatusCache.unlisted_key(share_token))
		for key in keys:
			frappe.cache.delete_value(key)


class StatusPage:
	"""Builds the public data of the status page. Only published incidents appear."""

	def get_status(self) -> dict:
		settings = frappe.get_cached_doc("Beacon Settings")
		incidents = self._get_open_published_incidents()
		impacts = self._get_impacts(incidents)
		days = self._get_days()
		visible_services = self._get_services()
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

	def get_incident(self, name: str) -> dict | None:
		incident = frappe.db.get_value("Beacon Incident", {"name": name, "visibility": "Public"}, "name")
		return self._get_detail(incident) if incident else None

	def get_unlisted_incident(self, token: str) -> dict | None:
		incident = frappe.db.get_value(
			"Beacon Incident",
			{"share_token": token, "visibility": "Unlisted"},
			["name", "status", "merged_into"],
			as_dict=True,
		)
		if incident and incident.status == "Merged" and incident.merged_into:
			target = frappe.db.get_value(
				"Beacon Incident",
				IncidentMerge.find_final_incident(incident.name),
				["name", "visibility"],
				as_dict=True,
			)
			incident = target if target and target.visibility != "Internal" else None
		return self._get_detail(incident.name) if incident else None

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
				"Beacon Service",
				filters={"name": ("in", [row.service for row in incident.affected_services])},
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
			"name": incident.name,
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

	def _get_impacts(self, incidents: list[frappe._dict]) -> dict[tuple[str, str], str]:
		"""The worst published impact for each (service, region). A blank region means every region."""
		impacts: dict[tuple[str, str], str] = {}
		active = [incident.name for incident in incidents if incident.status != "Scheduled"]
		rows = (
			frappe.get_all(
				"Beacon Incident Service",
				filters={"parent": ("in", active)},
				fields=["service", "region", "impact"],
			)
			if active
			else []
		)
		for row in rows:
			key = (row.service, row.region or "")
			impacts[key] = max(impacts.get(key, "Operational"), row.impact, key=STATUS_ORDER.index)
		return impacts

	def _get_services(self) -> list[frappe._dict]:
		services = frappe.get_all(
			"Beacon Service",
			filters={"show_on_status_page": 1, "enabled": 1},
			fields=["name", "title", "description", "scope", "query_region"],
			order_by="sort_order asc, title asc",
		)
		selection = RegionSelection("Beacon Service")
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
		days: dict[tuple[str, str], list[frappe._dict]] = {}
		for day in frappe.get_all(
			"Beacon Service Day",
			filters={"date": ("between", [add_days(getdate(), -HISTORY_DAYS + 1), getdate()])},
			fields=[
				"service",
				"region",
				"date",
				"good",
				"valid",
				"degraded_minutes",
				"outage_minutes",
				"partial_outage_minutes",
				"maintenance_minutes",
				"observed_minutes",
			],
			order_by="date asc",
		):
			days.setdefault((day.service, day.region), []).append(day)
		return days

	def _get_service(self, service: frappe._dict, region: str, impacts: dict, days: dict) -> dict:
		state = self.states.get((service.name, region)) or frappe._dict(status="Unknown")
		age = now_datetime() - get_datetime(state.checked_at) if state.checked_at else None
		stale = age is None or age < timedelta(0) or age > MEASUREMENT_MAX_AGE
		measured_status = "Unknown" if stale else state.status
		impact = max(
			impacts.get((service.name, region), "Operational"),
			impacts.get((service.name, ""), "Operational"),
			key=STATUS_ORDER.index,
		)
		if measured_status == "Unknown":
			status = "Unknown" if impact == "Operational" else impact
		else:
			status = max(measured_status, impact, key=STATUS_ORDER.index)
		history = days.get((service.name, region), [])
		good, valid = sum(day.good for day in history), sum(day.valid for day in history)
		return {
			"service": service.name,
			"title": service.title,
			"description": service.description,
			"status": status,
			"measurement_status": measured_status,
			"declared_impact": impact,
			"stale": stale,
			"checked_at": self._to_utc_iso(state.checked_at),
			"last_counted_minute": self._to_utc_iso(state.last_counted_minute),
			"history_days": HISTORY_DAYS,
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
				guid=f"{self.incident['name']}:{update['posted_at']}",
				pubDate=format_datetime(posted_at.replace(tzinfo=posted_at.tzinfo or UTC)),
			)
			item.find("guid").set("isPermaLink", "false")
		return ElementTree.tostring(feed, xml_declaration=True, encoding="utf-8")

	@staticmethod
	def _add(parent: ElementTree.Element, **children: str) -> None:
		for tag, text in children.items():
			ElementTree.SubElement(parent, tag).text = text
