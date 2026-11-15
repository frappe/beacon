app_name = "beacon"
app_title = "Beacon"
app_publisher = "Frappe"
app_description = "Incident manager, SLO control panel and status page for Frappe Cloud V2"
app_email = "developers@frappe.io"
app_license = "agpl-3.0"
app_logo_url = "/assets/beacon/images/logo.svg"

use_json_request_body = True
export_python_type_annotations = True
require_type_annotated_api_methods = True

# Beacon Health, a Desk Dashboard, shows charts that Beacon ships as an Insights workbook.
required_apps = ["insights"]

fixtures = [{"dt": "Role", "filters": [["name", "in", ["Beacon Admin", "Beacon Responder"]]]}]

app_include_js = "beacon.bundle.js"
app_include_css = "beacon.bundle.css"
notification_config = "beacon.core.alert.states.get_notification_config"

add_to_apps_screen = [
	{
		"name": "beacon",
		"title": "Beacon",
		"logo": app_logo_url,
		"route": "/desk/beacon",
	}
]

# The status page is a Vue app served at / and at its own routes.
home_page = "status"
website_route_rules = [
	{"from_route": "/incidents/<public_id>", "to_route": "status"},
	{"from_route": "/history", "to_route": "status"},
	{"from_route": "/subscription/<token>", "to_route": "status"},
]

after_install = [
	"beacon.core.setup.after_install",
	"beacon.core.definitions.create_built_in_rules",
]

after_migrate = [
	"beacon.core.setup.after_migrate",
	"beacon.core.definitions.create_built_in_rules",
]

scheduler_events = {
	"cron": {
		"* * * * *": [
			"beacon.core.jobs.run_schedule",
			"beacon.core.jobs.sync_sources",
			"beacon.core.jobs.evaluate_due_rules",
		],
	},
	"daily": ["beacon.core.jobs.delete_old_rows", "beacon.core.jobs.remind_postmortems"],
}
