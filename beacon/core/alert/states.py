from __future__ import annotations

OPEN_ALERT_STATUSES = ("Pending", "Firing")
SEVERITIES = ("Low", "Medium", "High", "Critical")
PAGED_SEVERITIES = ("High", "Critical")


class Severity:
	@staticmethod
	def is_higher(severity: str, than: str) -> bool:
		return SEVERITIES.index(severity) > SEVERITIES.index(than)


def get_notification_config() -> dict:
	"""Desk Connections show the open alerts beside the total."""
	return {"for_doctype": {"Beacon Alert": {"status": ("in", OPEN_ALERT_STATUSES)}}}
