import frappe

from beacon.beacon.doctype.beacon_notification.beacon_notification import BeaconNotification


@frappe.whitelist(allow_guest=True, methods=["POST"])
def status(notification: str, token: str, CallStatus: str, AccountSid: str = "", CallSid: str = "") -> None:
	"""Twilio reports each call status. An answered call acknowledges its incident."""
	if not frappe.db.exists("Beacon Notification", notification):
		raise frappe.PermissionError

	document = frappe.get_doc("Beacon Notification", notification)
	if not document.is_valid_callback(token, AccountSid, CallSid):
		raise frappe.PermissionError

	document.record_call_status(CallStatus)
