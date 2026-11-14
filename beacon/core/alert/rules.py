from __future__ import annotations

import frappe

BURN_KIND = "SLO Burn"


class AlertRules:
	"""Lookups shared by every job that treats SLO burn rules apart from cause rules."""

	@staticmethod
	def get_burn_rule_names() -> set[str]:
		return set(frappe.get_all("Beacon Alert Rule", filters={"kind": BURN_KIND}, pluck="name"))

	@staticmethod
	def get_burn_rule_name(service: str) -> str:
		return f"slo-burn-{service}"

	@staticmethod
	def get_resource_types(resource_type: str | None) -> set[str]:
		"""The types in a rule's comma-separated `resource_type`. An empty set takes every type."""
		return {name.strip() for name in (resource_type or "").split(",") if name.strip()}
