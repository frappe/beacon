# Beacon specification

This file is the code contract: where each behavior lives, the jobs, the extension points and the invariants. The [design](https://sites.tanmoysarkar.me/plans/beacon-incident-manager/) describes what Beacon does and why.

## Module map

Beacon is the only Frappe module. All DocTypes and their form scripts live in `beacon/beacon/doctype/`. Python packages group related behavior.

Every DocType name starts with `Beacon `. For example, `Beacon Incident` uses `beacon/beacon/doctype/beacon_incident/`.

| Package | Folder | Owns | Key files |
|---|---|---|---|
| Core | `beacon/core` | Scheduled work, record transactions, definitions, public status data, site setup | `scheduler.py`, `transaction.py`, `definitions.py`, `status_page.py`, `setup.py`, `jobs.py`, `heartbeat.py`, `exceptions.py` |
| Resource | `beacon/core/resource` | Resource sync, resource tree, region selection, ClickHouse access, region probe | `source.py`, `source_client.py`, `registry.py`, `regions.py`, `tree.py`, `clickhouse.py`, `region_probe.py` |
| Alert | `beacon/core/alert` | Rule evaluation, alert transitions, burn rule lookups, silence and maintenance suppression | `rule_evaluator.py`, `evaluation_schedule.py`, `reconciler.py`, `states.py`, `rules.py`, `suppression.py` |
| Incident | `beacon/core/incident` | Incident status machine, alert matching and coordination, recovery, merge, publication, escalation, maintenance, notifications, status page subscriptions | `lifecycle.py`, `matcher.py`, `coordinator.py`, `recovery.py`, `merge.py`, `publication.py`, `escalation.py`, `maintenance.py`, `outbox.py`, `notifiers.py`, `call_response.py`, `postmortem.py`, `subscription.py` |
| Service | `beacon/core/service` | Service measurements, damped status, minute history, error budget and burn alerts | `measurement.py`, `status.py`, `history.py`, `burn.py` |
| API | `beacon/api` | Guest status page methods and Twilio callbacks only | `status.py`, `twilio.py` |
| Status page | `frontend/` | The Vue app at `/` | `src/main.js`, `src/api.js`, `src/pages/` |
| Desk actions | `beacon/public/js/beacon.bundle.js` | `beacon.add_action`, `beacon.show_results` and `beacon.RecordView`, used by every form script | `document_action.js`, `record_view.js` |

## Desk dashboard

The Beacon app icon opens the native Workspace at `/desk/beacon`. Its Sidebar groups Operations, Health, Configuration and Logs. Logs start collapsed.

The public status page link sits directly below Dashboard. On phones, `beacon/public/css/beacon.bundle.css` places two Number Cards per row on this Workspace.

Six Number Cards count open incidents, firing alerts, active maintenance, active regions, services and monitored resources. Each card opens its filtered list. Open incidents exclude maintenance. Monitored resources exclude terminated resources and resources with monitoring disabled.

The Workspace uses native Quick Lists for open incidents and firing alerts. Shortcuts open service health, maintenance and on-call settings. Frappe applies document permissions to counts, lists and navigation.

**Beacon health**, under Health, opens the Desk Dashboard `Beacon Health` (`beacon/beacon/beacon_dashboard/`). Insights is a required app. Its island shows the Insights dashboard that the dashboard links, from the standard workbook `beacon/beacon/insights_workbook/beacon_health/beacon_health.json`. The charts read the site database for the last 30 days: incidents, average time to acknowledge and resolve, notification delay and failures by channel, the noisiest rules, and the SLI of each service. To change a chart, edit it in Insights in developer mode. Insights writes the workbook file back.

The definitions live in `beacon/beacon/workspace/`, `beacon/beacon/sidebar/` and `beacon/beacon/number_card/`. These records sync through normal migration. The dashboard has no separate frontend or custom API.

## Jobs

| Schedule | Entry point | Work |
|---|---|---|
| Every minute | `jobs.run_schedule` | `BeaconScheduler`: advances maintenance, resolves disabled alerts, queues each active region, advances escalations and sends the heartbeat. Beacon holds the heartbeat while a rule evaluation or a queued notification is more than 5 minutes late, so the external service also knows when the workers stop. |
| Every minute, per region | `jobs.run_region_schedule` | `RegionSchedule`: probes the region, measures services, refreshes suppression and coordinates incidents. |
| Every minute | `jobs.sync_sources` | `BeaconResourceSource.queue_due_syncs` queues `sync` for each enabled source with `frappe.enqueue_doc`. |
| Every minute | `jobs.evaluate_due_rules` | `RegionEvaluation.queue_due` queues one `jobs.evaluate_region` job per region with due evaluations. |
| Daily | `jobs.delete_old_rows` | `RecordRetention` removes expired records. |
| Daily | `jobs.remind_postmortems` | `Postmortem.remind_missing` posts to chat for each required postmortem that is still empty, from 3 to 30 days after the resolve. |
| After commit | `jobs.deliver_notification` | `NotificationDelivery` sends one Beacon Notification. When an email or call fails, it posts the error to chat. |
| After commit | `jobs.resolve_recovered_incidents` | `IncidentRecovery` closes incidents whose alerts all recovered. |
| After migrate | `definitions.create_built_in_rules` | Creates the built-in rules. |
| Sync Definitions in Beacon Settings | `definitions.DefinitionSync` | Reads `rules/*.sql`, `rules/*.http` and `services/*.sql` from each definition source on GitHub and applies them. Runs only on demand. |

Region, evaluation and source sync jobs use the `long` queue. Notification jobs use the `short` queue, so a page does not wait behind slow region runs. Scheduled and queued entry points run as Administrator through `run_as_admin`.

## Ownership

- DocTypes own document state, permissions and public actions. Actions use workflow objects where a workflow spans records.
- `RuleEvaluator`, `ResourceSync`, `IncidentCoordinator` and `ServiceMeasurement` own their domain workflows.
- `IncidentLifecycle` owns incident status changes. `NEXT_STATUSES` lists the allowed changes, and `move_to` does the work of the new status. `IncidentEscalation` owns the escalation field, and `AlertReconciler` owns alert status changes.
- `IncidentMatcher` decides which open incident an alert joins and does not read or write the database.
- `AlertReconciler` changes alerts only. It returns the incidents to check, and the caller runs `IncidentRecovery`. The alert package does not import the incident package.
- `RegionSelection` owns which active regions a rule or service runs in. `AlertRules` owns burn rule names.
- `jobs.py` owns queue entry points and queue options. Scheduler hooks delegate to workflow objects.
- `ResourceSourceClient`, `ClickHouseClient` and the `Notifier` subclasses own remote calls. These adapters do not decide incident or alert transitions.
- `DefinitionLoader` owns file definitions. `EvaluationSchedule` owns the set of required rule evaluation records.
- `CallResponse` applies verified phone responses. The Twilio API handles signature checks and XML responses.

## Extension points

- **Definition source:** a GitHub repository in Beacon Settings, with a branch and a folder that holds `rules/` and `services/`. The GitHub Token is optional and needed only for private repositories. Each app keeps the rules, services and runbooks for its own telemetry in `beacon/` of its repository. For example, Atlas keeps its host and service node rules there. Beacon has a built-in Region Unreachable rule and creates an SLO Burn rule for each service. All other rules and services come from definition sources. A sync reads every file first: a bad header, a name that another source owns, or a failing new or changed rule query stops that source before any write. A rule or service whose file is gone is turned off and marked Removed From Source. It comes back on when the file comes back. Another source can take a name over when the old source removed its file or is no longer configured, so a rule can move between repositories. Services come only from files, and so do the rules that a file defines: Desk can only change Enabled. A service's Enabled also turns its SLO burn rule on or off. A rule made in Desk is Custom, an SQL or HTTP rule that staff can change. A file with the same rule key takes a Custom rule over on the next sync. The sync result warns about each High or Critical rule without a `runbook_url`, because a page must tell the responder what to do.
- **Rule:** add `<folder>/rules/<rule-key>.sql` to a definition source. The header lines (`-- title:`, `-- severity:`, `-- resource_type:`, `-- interval:`, `-- pending_for:`, `-- resolve_after:`, `-- scope_includes_descendants:`, `-- runbook_url:`) set the Alert Rule fields. The query returns one row per failing resource with the columns `resource_id`, `alert_key`, `value`, `detail`. An optional `severity` column (`Low`, `Medium`, `High` or `Critical`) overrides the header severity for that row, so one rule can page at a hard limit and post at a soft limit. The header severity is the highest severity of the rule, so the runbook check applies. A Pending alert takes the severity of its current row. A Firing alert only escalates, and it then stores the value and detail of the row that made it worse. A pair of `resource_id` and `alert_key` occurs once. The limit is 50,000 rows.
- **HTTP rule:** add `<folder>/rules/<rule-key>.http` with only header lines: `-- title:`, `-- severity:`, `-- url:`, `-- region:`, and optionally `-- expected_status:` (200), `-- expected_text:`, `-- timeout:` (10 seconds), `-- pending_for:`, `-- resolve_after:`, `-- runbook_url:`. Each minute the region run of that region calls the URL from the Beacon site (`core/alert/http_check.py`), with no row lock, then raises or resolves the alert on the region resource. A region outage does not suppress it.
- **Service:** add `<folder>/services/<service>.sql` to a definition source. The header lines (`-- title:`, `-- description:`, `-- scope:`, `-- query_region:`, `-- slo_target:`, `-- sla_target:`, `-- show_on_status_page:`, `-- sort_order:`, `-- page_on_burn:`) set the Service fields. The SLI query returns one row per reporter with `resource_id`, `good`, `valid`, `last_ts`.
- **Resource Source:** the Base URL is the API root of a service. A regional service serves `GET <base>/beacon/resources` with `{"resources": [{resource_id, resource_type, parent_id, title, status}]}` and an `ETag`. Atlas serves it at `/api/atlas/beacon/resources`. Central serves `GET <base>/beacon/telemetry-ids` with `{"telemetry_ids": [{telemetry_id, region, resource_id}]}`. Beacon signs in with the Frappe API key of a user on the service (`Authorization: token key:secret`).
- **Notification channel:** subclass `Notifier` in `core/incident/notifiers.py`, implement `is_enabled` and `send`, and add it to `NOTIFIERS`.
- **Action:** add one whitelisted controller method that staff and Beacon both call. Decorate an action that only writes to the database with `@document_action` (`core/transaction.py`): a lock conflict rolls back, reloads the document and runs the action again up to 3 times. An action that makes a remote call uses `@frappe.whitelist`. The form calls it through `beacon.add_action`; scripts and chat-ops call `POST /api/v2/document/<DocType>/<name>/method/<method>`.

For example, acknowledge an incident with `POST /api/v2/document/Beacon%20Incident/<name>/method/acknowledge`.

## Coverage

Each rule file names the producer it reads, and lives in the repository of that producer. A rule about one app raises its alert on that app's resource, so an Atlas failure does not join an incident of every region resource.

| Area | Producer | Resource | Rules | Repository |
| --- | --- | --- | --- | --- |
| Atlas control plane | Atlas gauges and Error Logs (`datum.samples`, `datum.logs`, resource `atlas-<region>`), TLS certificate expiry | `atlas` | `atlas-*`, `tls-certificate-expiring` | `frappe/atlas` |
| Hosts | Atlas host inventory and sync age, metald build info, VM states and reconcile loops, host system metrics (ZFS, RAID, disks, memory errors, CPU, network, systemd, kernel limits), BMC readings and events, host logs | Host | `host-*`, `atlas-cannot-sync-host`, `vms-unavailable-on-host`, `metald-unhealthy`, `bmc-problems`, `vm-migrations-failed`, `repeated-errors`, service `servers` | `frappe/atlas` |
| Regional services | Atlas readiness probes, Vector on each node (load, disk, memory, proxy counters) | service node | `service-node-*`, `atlas-service-nodes-failed`, `proxy-error-spike`, service `networking` | `frappe/atlas` |
| Cargo | Cargo build and metadata backup gauges (hourly), Garage metrics, Cargo Error Logs | `cargo` | `cargo-*`, `object-storage-*` | `frappe/cargo` |
| Central | Central Error Logs, sent to the Datum of the region in the `telemetry_region` site config | Region | `central-errors` | `frappe/central` |
| Pilot | `pilot_sites` gauge and `notifications.json.log` | Server | `pilot-*`, service `sites` | `frappe/pilot` |

## Invariants

- Beacon only reads ClickHouse. Datum owns the schema, the `latest_value` and `counter_increase` views and the read-only `beacon` user.
- Datum stores `ts` in UTC, and the site runs in UTC. `SiteSetup.configure` sets the time zone on install and on every migrate. Desk shows times in the time zone of each user, as Frappe does. The status page shows UTC.
- A rule or SLI query error never resolves an alert. A failed fetch never removes a resource.
- `AlertReconciler` writes alerts only on transitions.
- After each evaluation and each scheduled region run, every Firing, unsuppressed alert belongs to an open incident. `IncidentCoordinator` owns this and holds one advisory lock per region.
- A service with an SLA Target also has the burn alert `sla`. It fires when the error rate is above `100 - sla_target` percent in the last hour and in the last 15 minutes.
- A burn window alerts only when Service Minutes cover half of it and it has 100 valid events. The error budget projects valid events to 30 days until 30 days are measured. A fast, medium or SLA burn alert pages only when, in one minute of its short window, 3 reporters were bad, or every reporter when fewer than 3 reported. Otherwise its severity is Low, because one bad reporter has its own resource alert.
- Beacon never publishes an incident and never reopens a public one.
- Status page methods return only published data and cache each result for 60 seconds. `StatusCache.clear` removes the keys an action changes.
- A visitor can follow one published incident by email or RSS. `subscribe` (rate limited) stores a Pending `Beacon Incident Subscriber` and emails a confirmation link. The email links open `/subscription/<token>`, and that page posts to `change_subscription`, so a mail client that opens links ahead changes nothing. `IncidentSubscription.queue_new_updates` runs on each incident save and queues `notify_subscribers` for each new public update, which emails each Confirmed subscriber with an unsubscribe link. `get_incident_feed` returns the public updates as RSS 2.0.
- A notification row is written in the caller's transaction and sent after commit. No remote call runs under a database lock.
- A job that works on many records runs each record through `RecordTransaction` (`core/transaction.py`). Each record has its own transaction. A lock conflict (deadlock, MariaDB 11.8 snapshot isolation error 1020, lock wait timeout) rolls back and retries the record up to 3 times. Another failure rolls back that record and writes an Error Log with the record as reference. One record never stops the loop, and each step of a region run is separate.
- Remote calls (ClickHouse, Twilio, resource sources) run before the rows are locked. Then `RecordTransaction.start_new` starts a new transaction, and the work locks its rows before it reads them. A retry repeats only the database write, so a notification is never sent twice. A changed rule query is checked in ClickHouse before the save locks the rule row.
- Lock order: the incident row before its alert rows. Incident actions, the coordinator, merge, recovery and phone acknowledge follow it. `IncidentRecovery` runs as its own job after the caller commits, so it never waits for an incident while it holds alert locks.
- Advisory locks serialize work that spans a remote call: one coordinator run per region, one run per rule evaluation, one sync per resource source.
- An incident records who found the problem first (Detected By). Beacon sets Alert for the incidents it opens. On resolve, `Postmortem` marks Postmortem Required for a public or Critical incident, one longer than 1 hour, or one that a customer found, and starts an empty RCA from its template. The Post Update, Resolve and Publish dialogs start with the usual message for the status.
- A failure that repeats every minute writes one Error Log, not one per run. A rule evaluation logs when its state changes. A resource source logs its first failed fetch and posts to chat after 5 failed fetches in a row. A region probe logs only when the region was not already Unreachable.
- Form actions reload the form before `frm.call`, so they send the current record. A draft action, such as a test, runs on the form as edited and saves nothing. Read-only methods use `@frappe.read_only()`.
- Affected resources are computed when a person asks for them: the scope rows plus the resources of the incident's alerts, with child resources when the rule includes them. Nothing is stored for each resource. When a region is unreachable, the region alert covers only the region resource.
- During an outage the coordinator posts one chat message for each incident in each run, with the count of the alerts that joined.
- Each function has a cyclomatic complexity of 7 or less. Ruff checks it (`C901`).

## Development

- Build the status page with `yarn build` in the app folder. It writes `beacon/public/frontend/` and `beacon/www/status.html`. Both are build output and are not in git.
- Use `yarn dev` in `frontend/` for a Vite server that proxies `/api` to `beacon.localhost:8000`.
