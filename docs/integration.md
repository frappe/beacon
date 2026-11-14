# Integration

A service team does some of these steps so that Beacon watches its service. Most services need only the first three.

## 1. Report what you watch, as counts

When your service watches things below it, send to Datum how many there are and how many fail, each minute. Do not add a label for each item. A site, a VM or a request is never a label value. A host or a service node can be a label value, because there are few of them.

For example, Pilot sends one gauge for each site state on each VM:

```text
pilot_sites{state="up"}            398
pilot_sites{state="app_error"}       1
pilot_sites{state="platform_down"}   0
pilot_sites{state="maintenance"}     1
```

Send all states of one minute with the same timestamp. A Go service can expose Prometheus metrics on localhost, and the Vector agent on the machine sends them to Datum. A service that writes only an access log can turn it into counters with Vector.

## 2. Keep your Beacon files in your repository

Each app keeps its Beacon files in `beacon/` of its own repository:

```text
beacon/
  rules/<rule-key>.sql       cause rules
  rules/<rule-key>.http      HTTP checks
  services/<service>.sql     SLI queries
  runbooks/<rule-key>.md     the steps for each rule that pages
```

Add each repository as a Definition Source in Beacon Settings. Set its branch and the folder `beacon`. Select Sync Definitions to read its files. If a file has an invalid header, a name that another source owns, or a failing new or changed SQL query, Beacon applies no definitions from that source. If a rule or service file is missing, Beacon disables that record and marks it Removed From Source. Beacon enables it again when the file comes back. A source can take a name when its old source removed the file or is no longer configured, so a rule can move between repositories.

Desk can only turn a file rule on or off. To change a rule, change its file.

## 3. Add a cause rule only when it points to one fix

See [Alerts](alerts.md) for the rule contract. Use the Datum views, so that counters and freshness are correct:

| View | Returns |
| --- | --- |
| `datum.latest_value(metric, window)` | `resource_id`, `labels`, `value`, `last_ts`: the newest sample of each series in the window. |
| `datum.counter_increase(metric, window)` | `resource_id`, `labels`, `samples`, `increase`: the growth of each counter series, with resets handled. |

Rules run each minute in each region. Read `datum.samples_recent` (2 days) for short windows. Read `datum.samples` only for a long window, and limit it by `resource_id` and `metric`, because they start its sort key.

Some patterns help:

- **Silence.** Start from a list of what must report, such as the hosts that Atlas runs, and join the last sample. A missing sample is silent. A rule that starts from the samples resolves when the old samples expire.
- **Soft and hard limits.** Return a `severity` column, so one rule posts at the soft limit and pages at the hard limit.
- **Spikes.** Compare a short window with a long one, and also alert on a high rate in the long window. Else a steady failure hides in its own baseline.
- **Noise.** Leave out errors that the caller causes, such as permission errors, and errors that another rule already covers.

A High or Critical rule needs a `runbook_url`. The sync warns about each one without it.

## 4. When customers use your service, define an SLI

Add `beacon/services/<service>.sql`. The header sets the title, the scope (regional or global), the SLO target and the status page fields. The query returns one row for each reporter seen in the last 30 minutes: `resource_id`, `good`, `valid`, `last_ts`. Count only failures that the platform causes. See [Services and SLOs](services.md).

## 5. When your service lists resources, serve a resource list

A regional service that runs infrastructure serves `GET <base>/beacon/resources`:

```json
{"resources": [{"resource_id": "h12", "resource_type": "Host", "parent_id": "", "title": "h12", "status": "Active"}]}
```

The response has an `ETag`. Beacon signs in with the Frappe API key of a user on the service. The status is one of Pending, Active, Suspended and Terminated. Beacon marks a resource as Terminated when it is missing from 2 good fetches. When one fetch would remove more than 10% of the resources, Beacon holds the removal and an admin must apply it.

Central can also serve `GET <base>/beacon/telemetry-ids` with a `telemetry_ids` list. Beacon uses this list to map telemetry IDs onto resources that already exist. It does not add or remove resources. Beacon requests the full list every 15 minutes and uses the ETag between full requests.
