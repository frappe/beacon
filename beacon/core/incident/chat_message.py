from __future__ import annotations

import frappe
from frappe.utils import get_url_to_form

SEVERITY_MARKS = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🔵"}
DETAIL_LENGTH = 120


class ChatMessage:
	"""Markdown for the chat. The incident name is the link to the incident."""

	@staticmethod
	def link(incident: str) -> str:
		return f"[{incident}]({get_url_to_form('Beacon Incident', incident)})"

	@staticmethod
	def header(severity: str, incident: str, region: str) -> str:
		return f"{SEVERITY_MARKS.get(severity, '')} **{severity}** incident {ChatMessage.link(incident)} in {region}"

	@staticmethod
	def alert_table(alerts: list[frappe._dict], with_rule: bool = False) -> str:
		"""One row for each alert. Each alert has rule_title, resource_title, value and detail."""
		columns = ["Alert"] if with_rule else []
		columns += ["Resource", "Value", "Detail"]
		lines = [f"| {' | '.join(columns)} |", f"|{'---|' * len(columns)}"]
		for alert in alerts:
			cells = [alert.rule_title] if with_rule else []
			cells += [alert.resource_title, f"{alert.value or 0:g}", alert.detail or ""]
			lines.append(f"| {' | '.join(ChatMessage._cell(cell) for cell in cells)} |")
		return "\n".join(lines)

	@staticmethod
	def _cell(text: str) -> str:
		# A pipe or a line break in rule output would break the table.
		text = " ".join(text.split()).replace("|", "\\|")
		return text if len(text) <= DETAIL_LENGTH else f"{text[: DETAIL_LENGTH - 1]}…"
