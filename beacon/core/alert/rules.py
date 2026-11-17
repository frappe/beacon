from __future__ import annotations

import frappe

BURN_KIND = "SLO Burn"
BURN_RESOLVE_AFTER_SECONDS = 300


class AlertRules:
	"""Lookups shared by every job that treats SLO burn rules apart from cause rules."""

	@staticmethod
	def get_burn_rule_names() -> set[str]:
		return set(frappe.get_all("Beacon Alert Rule", filters={"kind": BURN_KIND}, pluck="name"))

	@staticmethod
	def get_problem_text(kind: str, title: str, description: str | None) -> tuple[str, str]:
		"""The title and explanation that responders see. A burn names the error budget problem."""
		if kind != BURN_KIND:
			return title, description or ""
		return (
			f"{title} error budget burning fast",
			f"The measured error rate for {title} is above its target."
			" The 30-day error budget is burning fast.",
		)

	@staticmethod
	def get_resource_types(resource_type: str | None) -> set[str]:
		"""The types in a rule's comma-separated `resource_type`. An empty set takes every type."""
		return {name.strip() for name in (resource_type or "").split(",") if name.strip()}
