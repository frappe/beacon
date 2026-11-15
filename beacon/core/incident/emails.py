from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe.utils import format_datetime, get_fullname, get_url_to_form

if TYPE_CHECKING:
	from beacon.beacon.doctype.beacon_settings.beacon_settings import BeaconSettings

ALERTS_SHOWN = 10
SEVERITY_PILLS = {"Critical": "critical", "High": "high", "Medium": "medium"}


class IncidentEmail:
	"""The subject and template arguments of the report card emails about one incident. Every value comes
	from a stored field. The templates escape them."""

	def __init__(self, incident: str):
		self.incident = frappe.get_doc("Beacon Incident", incident)

	def get_responder_email(self, page_round: int, settings: BeaconSettings) -> tuple[str, dict]:
		"""A resolved incident gets the closing email. A page round gets the escalation email, the only one
		that speaks of a call. Else the email announces a new incident to every active responder."""
		incident = self.incident
		facts = [("Incident", incident.name), ("Started", self._format(incident.creation))]
		if incident.status == "Resolved":
			acknowledged = incident.acknowledged_by and (
				f"{get_fullname(incident.acknowledged_by)}, {self._format(incident.acknowledged_at)}"
			)
			args = {
				"pill": "Resolved",
				"pill_class": "resolved",
				"note": incident.updates[-1].message if incident.updates else "",
				"instruction": "",
				"facts": [
					*facts,
					("Resolved", self._format(incident.resolved_at)),
					("Acknowledged", acknowledged),
				],
			}
			return f"[Resolved][{incident.name}] {incident.title}", self._with_incident(args)

		facts.append(("Regions", ", ".join(self._get_regions())))
		args = {
			"pill": f"{incident.severity} · {incident.status}",
			"pill_class": SEVERITY_PILLS.get(incident.severity, ""),
		}
		if page_round:
			rounds = f" of {settings.max_rounds}" if incident.severity == "High" else ""
			args["instruction"] = (
				"Beacon is calling you now. Answer the call, or open the incident and choose Acknowledge. If nobody"
				f" acknowledges in {settings.get_backup_after_minutes(incident.severity)} minutes, Beacon calls the next responder."
			)
			args["facts"] = [*facts, ("Page round", f"{page_round}{rounds}")]
		else:
			args["instruction"] = "A new incident started. Open it and choose Acknowledge if you take it."
			args["facts"] = facts
		args["alerts"] = self._get_alert_titles()
		return f"[{incident.severity}][{incident.name}] {incident.title}", self._with_incident(args)

	def get_update_email(self, update: dict, page_url: str, unsubscribe_url: str) -> tuple[str, dict]:
		"""One subject for the whole incident, so mail clients keep every update in one thread."""
		incident = self.incident
		facts = [
			("Posted", self._format(update.posted_at)),
			("Started", self._format(incident.creation)),
			("Resolved", self._format(incident.resolved_at) if incident.status == "Resolved" else None),
		]
		args = {
			"brand": frappe.get_cached_doc("Beacon Settings").status_page_title,
			"center": True,
			"title": incident.title,
			"status": update.status,
			"pill_class": "resolved" if update.status == "Resolved" else "",
			"message": update.message,
			"facts": facts,
			"services": self._get_services(),
			"url": page_url,
			"unsubscribe_url": unsubscribe_url,
		}
		return f"Incident: {incident.title}", args

	def _with_incident(self, args: dict) -> dict:
		return {
			"brand": "Beacon",
			"title": self.incident.title,
			"url": get_url_to_form("Beacon Incident", self.incident.name),
			"note": "",
			"alerts": [],
			**args,
		}

	def _get_regions(self) -> list[str]:
		regions = {row.region for row in self.incident.scope} | set(
			frappe.get_all(
				"Beacon Alert", filters={"incident": self.incident.name}, pluck="region", distinct=True
			)
		)
		titles = self._get_titles("Beacon Region")
		return sorted(titles.get(region, region) for region in regions if region)

	def _get_alert_titles(self) -> list[str]:
		titles = frappe.get_all(
			"Beacon Alert", filters={"incident": self.incident.name}, pluck="title", order_by="fired_at asc"
		)
		more = len(titles) - ALERTS_SHOWN
		return titles[:ALERTS_SHOWN] + ([f"and {more} more"] if more > 0 else [])

	def _get_services(self) -> list[dict]:
		services, regions = self._get_titles("Beacon Service"), self._get_titles("Beacon Region")
		return [
			{
				"title": " · ".join(filter(None, (services.get(row.service), regions.get(row.region)))),
				"impact": row.impact,
			}
			for row in self.incident.affected_services
		]

	@staticmethod
	def _get_titles(doctype: str) -> dict[str, str]:
		return dict(frappe.get_all(doctype, fields=["name", "title"], as_list=True))

	@staticmethod
	def _format(value) -> str | None:
		return f"{format_datetime(value, 'd MMM yyyy, HH:mm')} UTC" if value else None
