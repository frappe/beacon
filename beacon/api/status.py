import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import get_url
from werkzeug.wrappers import Response

from beacon.core.incident.subscription import IncidentSubscription
from beacon.core.status_page import IncidentFeed, StatusCache, StatusPage


@frappe.whitelist(allow_guest=True, methods=["GET"])
@frappe.read_only()
def get_status() -> dict:
	return StatusCache.get_or_build(StatusCache.STATUS, StatusPage().get_status)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@frappe.read_only()
def get_history() -> dict:
	return StatusCache.get_or_build(StatusCache.HISTORY, StatusPage().get_history)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@frappe.read_only()
def get_incident(public_id: str) -> dict:
	"""An empty result is cached too, so unknown links cannot reach the database every time."""
	incident = StatusCache.get_or_build(
		StatusCache.incident_key(public_id), lambda: StatusPage().get_incident(public_id) or {}
	)
	if not incident:
		raise frappe.DoesNotExistError
	return incident


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=3600)
def subscribe(email: str, public_id: str) -> None:
	incident = StatusPage().find_incident(public_id)
	if not incident:
		raise frappe.DoesNotExistError
	IncidentSubscription(incident).subscribe(email)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=3600)
def change_subscription(token: str, action: str) -> str:
	"""The email links open a status page route, which posts here. A mail client that opens links ahead does not change anything."""
	return IncidentSubscription.change(token, action)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@frappe.read_only()
def get_incident_feed(public_id: str) -> Response:
	incident = get_incident(public_id)
	return Response(
		IncidentFeed(incident, get_url(f"/incidents/{public_id}")).build(), mimetype="application/rss+xml"
	)
