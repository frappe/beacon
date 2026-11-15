import frappe

from beacon.core.incident.call_response import CallResponse


@frappe.whitelist(allow_guest=True, methods=["POST"])
def status(notification: str, token: str, CallStatus: str, AccountSid: str = "", CallSid: str = "") -> None:
	"""Twilio reports each call status. An answered call acknowledges its incident."""
	response = CallResponse(notification)
	if not response.is_valid_callback(token, AccountSid, CallSid):
		raise frappe.PermissionError

	response.record_status(CallStatus)
