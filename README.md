<p align="center">
  <img src="beacon/public/images/logo.svg" alt="Beacon" width="80" height="80">
</p>

<h1 align="center">Beacon</h1>

<p align="center">
  <strong>Incident manager, SLO control panel and status page for Frappe Cloud</strong>
</p>

<p align="center">
  <a href="SPEC.md">Specification</a> ·
  <a href="https://sites.tanmoysarkar.me/plans/beacon-incident-manager/">Design</a> ·
  <a href="CLAUDE.md">Contributor guide</a>
</p>

Beacon watches every Frappe Cloud region. It reads the telemetry of each region from Datum, raises alerts, groups them into incidents and pages the responder. It measures each service against its SLO and shows customers a public status page.

Beacon is one Frappe app on one global site. It only reads Datum. It keeps alert, incident and SLO state in MariaDB, and it serves the status page at `/`.

Beacon is in pre-production.

## How it works

| Part | What it does |
| --- | --- |
| Resources | Each region's Atlas lists what the region runs: hosts, VMs, service nodes and Cargo. Beacon keeps them as a tree. |
| Rules | Each app keeps its alert rules in `beacon/rules/` of its own repository. A rule is one SQL query on Datum. |
| Incidents | Firing alerts join an open incident. Beacon pages by phone, chat and email, and escalates until a person acknowledges. |
| Services | An SLI query measures each service each minute. Burn-rate alerts protect the error budget. |
| Status page | A person decides what customers see. Customers follow one incident by email or RSS. |

## Start here

| Task | Guide |
| --- | --- |
| Find where code lives | [Specification](SPEC.md) |
| Add an alert rule or a service | [Extension points](SPEC.md#extension-points) |
| See which rules cover which app | [Coverage](SPEC.md#coverage) |
| Make a code change | [Contributor guide](CLAUDE.md) |

## Development

```sh
cd $PATH_TO_YOUR_BENCH
bench get-app git@github.com:frappe/beacon.git --branch develop
bench --site beacon.localhost install-app beacon
```

Build the status page with `yarn build` in the app folder. Use `yarn dev` in `frontend/` for a Vite server that proxies `/api` to `beacon.localhost:8000`.

Run `pre-commit install` once. Pre-commit runs ruff, eslint and prettier.

## Contributing

1. [Issue Guidelines](https://github.com/frappe/erpnext/wiki/Issue-Guidelines)
2. [Report Security Vulnerabilities](https://frappe.io/security)

<br>
<br>
<div align="center">
	<a href="https://frappe.io" target="_blank">
		<picture>
			<source media="(prefers-color-scheme: dark)" srcset="https://frappe.io/files/Frappe-white.png">
			<img src="https://frappe.io/files/Frappe-black.png" alt="Frappe Technologies" height="28"/>
		</picture>
	</a>
</div>
