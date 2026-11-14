from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from beacon.core.resource.regions import RegionSelection


class EvaluationSchedule:
	"""Keeps one Rule Evaluation for each enabled SQL rule and each region it runs in."""

	def sync(self) -> None:
		selection = RegionSelection("Beacon Alert Rule")
		rules = frappe.get_all("Beacon Alert Rule", filters={"kind": "SQL", "enabled": 1}, pluck="name")
		wanted = {(rule, region) for rule in rules for region in selection.get_regions(rule)}
		existing = {
			(row.rule, row.region): row.name
			for row in frappe.get_all("Beacon Rule Evaluation", fields=["name", "rule", "region"])
		}

		now = now_datetime()
		for rule, region in wanted - existing.keys():
			evaluation = frappe.get_doc(
				{
					"doctype": "Beacon Rule Evaluation",
					"rule": rule,
					"region": region,
					"state": "Healthy",
					"next_run_at": now,
				}
			)
			# Two saves can sync at once. The unique (rule, region) index keeps one row.
			evaluation.insert(ignore_permissions=True, ignore_if_duplicate=True)

		for key in existing.keys() - wanted:
			frappe.delete_doc("Beacon Rule Evaluation", existing[key], ignore_permissions=True, force=True)
