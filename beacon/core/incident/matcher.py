from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import frappe

from beacon.core.resource.registry import ResourceMatch

# An incident this young, or still Triggered, takes new alerts of its rules. An older one does not.
YOUNG_INCIDENT = timedelta(minutes=30)


@dataclass
class OpenIncident:
	"""What matching needs to know about one open incident in one region."""

	name: str
	status: str
	visibility: str
	acknowledged_at: datetime | None
	creation: datetime
	matches: list[ResourceMatch] = field(default_factory=list)
	# The rules of its alerts, also of the alerts that recovered.
	rules: set[str] = field(default_factory=set)
	# The resources of the firing cause alerts, and of the bad reporters of the firing burn alerts with their parents.
	cause_resources: set[str] = field(default_factory=set)
	burn_resources: set[str] = field(default_factory=set)
	has_firing_cause: bool = False
	has_firing_burn: bool = False

	@property
	def can_give_alerts(self) -> bool:
		"""A new incident that covers its alerts can take them: nobody works on it and no customer sees it."""
		return self.visibility == "Internal" and not self.acknowledged_at

	def covers(self, resource: frappe._dict | None) -> bool:
		# Every region-level rule reports on the region resource, so a scope on it would join unrelated rules.
		if not resource or resource.resource_type == "Region":
			return False
		return any(match.covers(resource) for match in self.matches)

	def is_young(self, now: datetime) -> bool:
		return self.creation > now - YOUNG_INCIDENT

	def add_alert(self, rule: str, is_burn: bool, firing_resources: set[str] | None) -> None:
		"""firing_resources is None for an alert that no longer fires."""
		self.rules.add(rule)
		if firing_resources is None:
			return

		if is_burn:
			self.has_firing_burn = True
			self.burn_resources.update(firing_resources)
		else:
			self.has_firing_cause = True
			self.cause_resources.update(firing_resources)


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

	def find(self, resource: frappe._dict, rule: str, is_burn: bool, resources: set[str]) -> Placement | None:
		"""In order: an incident that covers the resource, a related cause and burn pair, then an incident
		with an alert of the same rule. resources are the alert's resource for a cause, and the bad
		reporters with their parents for a burn."""
		covering = next((item for item in self.incidents if item.covers(resource)), None)
		if covering:
			return Placement(covering)
		return self._find_pair(is_burn, resources) or self._find_group(rule)

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
				if item.has_firing_burn and not item.has_firing_cause and item.burn_resources & resources
			),
			None,
		)
		return Placement(burn_only, add_scope=True) if burn_only else None

	def _find_group(self, rule: str) -> Placement | None:
		incident = next(
			(
				item
				for item in self.incidents
				if rule in item.rules and (item.status == "Triggered" or item.is_young(self.now))
			),
			None,
		)
		return Placement(incident) if incident else None
