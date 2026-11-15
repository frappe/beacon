from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import frappe

from beacon.core.resource.registry import ResourceMatch

# An incident this young, or still Triggered, takes new alerts of its group. An older one does not.
YOUNG_INCIDENT = timedelta(minutes=30)


@dataclass
class OpenIncident:
	"""What matching needs to know about one open incident in one region."""

	name: str
	status: str
	visibility: str
	acknowledged_at: datetime | None
	creation: datetime
	group_keys: set[str]
	matches: list[ResourceMatch]
	cause_alerts: list[str] = field(default_factory=list)
	burn_alerts: list[str] = field(default_factory=list)
	# The resources of the cause alerts, and of the bad reporters of the burn alerts with their parents.
	cause_resources: set[str] = field(default_factory=set)
	burn_resources: set[str] = field(default_factory=set)

	def covers(self, resource: frappe._dict | None) -> bool:
		return bool(resource) and any(match.covers(resource) for match in self.matches)

	def is_young(self, now: datetime) -> bool:
		return self.creation > now - YOUNG_INCIDENT

	def add_alert(self, alert: str, is_burn: bool, resources: set[str]) -> None:
		(self.burn_alerts if is_burn else self.cause_alerts).append(alert)
		(self.burn_resources if is_burn else self.cause_resources).update(resources)


@dataclass(frozen=True)
class Placement:
	"""The incident an alert joins. add_scope is set when the incident must also cover the alert's resource."""

	incident: OpenIncident
	add_scope: bool = False


class IncidentMatcher:
	"""Decides which open incident an orphan alert joins. Reads nothing and writes nothing."""

	def __init__(self, incidents: list[OpenIncident], now: datetime):
		self.incidents = incidents
		self.now = now

	def find(
		self, resource: frappe._dict, group_key: str, is_burn: bool, resources: set[str]
	) -> Placement | None:
		"""In order: an incident that covers the resource, a related cause and burn pair, then the alert's group.
		resources are the alert's resource for a cause, and the bad reporters with their parents for a burn."""
		covering = next((item for item in self.incidents if item.covers(resource)), None)
		if covering:
			return Placement(covering)
		return self._find_pair(is_burn, resources) or self._find_group(group_key)

	def _find_pair(self, is_burn: bool, resources: set[str]) -> Placement | None:
		"""A burn joins an incident with a cause on one of its bad reporters or their parents. A cause joins a
		burn-only incident that such a burn opened, and widens its scope. Unrelated alerts do not pair."""
		if is_burn:
			incident = next((item for item in self.incidents if item.cause_resources & resources), None)
			return Placement(incident) if incident else None

		burn_only = next(
			(
				item
				for item in self.incidents
				if item.burn_alerts and not item.cause_alerts and item.burn_resources & resources
			),
			None,
		)
		return Placement(burn_only, add_scope=True) if burn_only else None

	def _find_group(self, group_key: str) -> Placement | None:
		incident = next(
			(
				item
				for item in self.incidents
				if group_key in item.group_keys and (item.status == "Triggered" or item.is_young(self.now))
			),
			None,
		)
		return Placement(incident) if incident else None
