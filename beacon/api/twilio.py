import base64
import hashlib
import hmac
from xml.sax.saxutils import escape, quoteattr

import frappe
from frappe.utils import get_url
from werkzeug.wrappers import Response

from beacon.core.incident.call_response import CallResponse


@frappe.whitelist(allow_guest=True, methods=["POST"])
def voice(notification: str) -> Response:
	"""Twilio asks what to say when the call is answered: the incident, then press 1 to acknowledge."""
	_check_signature()
	message = CallResponse(notification).get_message()
	action = get_url(f"/api/method/beacon.api.twilio.gather?notification={notification}")
	return _twiml(
		f'<Gather numDigits="1" action={quoteattr(action)}><Say>Beacon. {escape(message)}. Press 1 to acknowledge.</Say></Gather>'
		"<Say>No key was pressed. Goodbye.</Say>"
	)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def gather(notification: str, Digits: str | None = None) -> Response:
	"""Only the key 1 acknowledges. A repeated callback changes nothing."""
	_check_signature()
	if Digits != "1":
		return _twiml("<Say>Not acknowledged. Goodbye.</Say>")

	CallResponse(notification).acknowledge()
	return _twiml("<Say>Acknowledged. Thank you.</Say>")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def status(CallSid: str, CallStatus: str) -> None:
	_check_signature()
	response = CallResponse.from_call_sid(CallSid)
	if response:
		response.record_status(CallStatus)


def _check_signature() -> None:
	"""Twilio signs the full URL plus the sorted form fields with the auth token."""
	token = (
		frappe.get_cached_doc("Beacon Settings").get_password("twilio_auth_token", raise_exception=False)
		or ""
	)
	request = frappe.request
	payload = request.url + "".join(f"{key}{request.form[key]}" for key in sorted(request.form))
	expected = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
	if not token or not hmac.compare_digest(expected, request.headers.get("X-Twilio-Signature", "")):
		raise frappe.PermissionError


def _twiml(body: str) -> Response:
	return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', mimetype="text/xml")
