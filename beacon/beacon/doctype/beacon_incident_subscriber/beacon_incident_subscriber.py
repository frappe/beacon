import frappe
from frappe.model.document import Document


class BeaconIncidentSubscriber(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		confirmed_at: DF.Datetime | None
		email: DF.Data
		incident: DF.Link
		status: DF.Literal["Pending", "Confirmed", "Unsubscribed"]
		token: DF.Data | None
	# end: auto-generated types


def on_doctype_update() -> None:
	frappe.db.add_unique("Beacon Incident Subscriber", ["incident", "email"])
