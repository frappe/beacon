from __future__ import annotations

from datetime import datetime

import frappe
from frappe.utils import get_url_to_form

SEVERITY_MARKS = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🔵"}
DETAIL_LENGTH = 120
ALERTS_SHOWN = 5


class ChatMessage:
	"""Markdown for the chat. The incident name is the link to the incident."""

	@staticmethod
	def link(incident: str) -> str:
		return f"[{incident}]({get_url_to_form('Beacon Incident', incident)})"

	@staticmethod
	def header(severity: str, incident: str, region: str) -> str:
		return f"{SEVERITY_MARKS.get(severity, '')} **{severity}** incident {ChatMessage.link(incident)} in {region}"

	@staticmethod
	def alert_table(alerts: list[frappe._dict], with_rule: bool = False, with_lasted: bool = False) -> str:
		"""One row for each of the first ALERTS_SHOWN alerts, from rule_title, resource_title, detail and lasted."""
		columns = ["Alert"] if with_rule else []
		columns += ["Resource", "Detail"] + (["Lasted"] if with_lasted else [])
		lines = [f"| {' | '.join(columns)} |", f"|{'---|' * len(columns)}"]
		for alert in alerts[:ALERTS_SHOWN]:
			cells = [alert.rule_title] if with_rule else []
			cells += [alert.resource_title, alert.detail or ""] + ([alert.lasted] if with_lasted else [])
			lines.append(f"| {' | '.join(ChatMessage._cell(cell) for cell in cells)} |")
		if len(alerts) > ALERTS_SHOWN:
			lines.append(f"\nand {len(alerts) - ALERTS_SHOWN} more")
		return "\n".join(lines)

	@staticmethod
	def duration(start: datetime, end: datetime) -> str:
		hours, minutes = divmod(int((end - start).total_seconds() // 60), 60)
		return f"{hours} h {minutes} min" if hours else f"{minutes} min"

	@staticmethod
	def _cell(text: str) -> str:
		# A pipe or a line break in rule output would break the table.
		text = " ".join(text.split()).replace("|", "\\|")
		return text if len(text) <= DETAIL_LENGTH else f"{text[: DETAIL_LENGTH - 1]}…"
