import frappe

SERVICE_FIELDS = (
	"description",
	"enabled",
	"is_removed",
	"major_outage_below",
	"measurement",
	"page_on_burn",
	"query_region",
	"scope",
	"show_on_status_page",
	"sla_target",
	"sli_query",
	"slo_target",
	"sort_order",
	"source",
	"status_page_group",
	"title",
)


def execute():
	"""A service is now its SLO Burn Alert Rule. Rename each `slo-burn-<service>` rule to the service name,
	so alerts keep their rule and the status history finds its service, then copy the service onto it."""
	if not frappe.db.table_exists("Beacon Service"):
		return
	for service in frappe.db.sql("select * from `tabBeacon Service`", as_dict=True):
		old_name = f"slo-burn-{service.name}"
		if frappe.db.exists("Beacon Alert Rule", service.name):
			continue
		if not frappe.db.exists("Beacon Alert Rule", old_name):
			raise frappe.ValidationError(f"Service {service.name} has no burn rule {old_name}")
		frappe.rename_doc("Beacon Alert Rule", old_name, service.name, force=True, show_alert=False)
		values = {field: service.get(field) for field in SERVICE_FIELDS}
		frappe.db.set_value("Beacon Alert Rule", service.name, {**values, "rule_key": service.name})
		frappe.db.sql(
			"""update `tabBeacon Region Item` set parenttype = 'Beacon Alert Rule'
			where parenttype = 'Beacon Service' and parent = %s""",
			service.name,
		)
