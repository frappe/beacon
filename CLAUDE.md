# Beacon Repository Guide

## Session continuity

- [MANDATORY] Follow this file and the [engineering playbook](llm/engineering-playbook/SKILL.md) for every task. This file wins on conflict.
- [MANDATORY] Read this file and `SKILL.md` at the start of every session, and again after context compaction, summarization, restart, or a resumed session, before you continue. Then read the playbook references that the task needs.
- [MANDATORY] Keep a reference to this file and `SKILL.md` in every handover or context summary, so the next context reloads both. Do not rely on remembered rules.

Beacon is the incident manager, SLO control panel and public status page for Frappe Cloud V2. It is one Frappe app on one global site. It reads each region's ClickHouse (Datum), keeps alert, incident and SLO state in MariaDB, and serves the status page at `/`.

## Start here

Read the [root specification](SPEC.md) for the module map, the jobs and the invariants. Read the [design](docs/README.md) before you change behavior. The design describes what Beacon does; `SPEC.md` describes where the code is.

## Core principles

- Keep changes small, direct, and in the package that owns the behavior.
- When the user states a problem without a solution direction, investigate first, ask focused questions, and offer options with a recommendation before you build.
- Keep decision-making visible to the user. Before implementation, explain the proposed design, assumptions, ownership, state, dependencies, error flow, risks, and important trade-offs. Proceed after the user agrees with the plan.
- Do not change unrelated dirty files, generated artifacts, or local data.
- Do not add plan files, such as `plan_*.md`.
- Prefer clear, explicit code over clever code and unnecessary abstraction.
- Store mutable and temporary state in the object or job that owns its lifecycle. Do not duplicate mutable state across owners.
- Fail near the cause. Retry only operations that are safe to repeat.
- Make code safe for concurrent jobs and Desk users. Before handover, check for stale document saves (`TimestampMismatchError`), lock wait timeouts, deadlocks and duplicate rows. Do not call a remote service (ClickHouse, a Resource Source, Twilio, a webhook) while you hold a database lock.
- Mandatory: Working code is not enough. Write code that a new engineer can read, understand and change easily.
- Don't repeat yourself. Integrations of one kind, such as notifiers, extend one base class and implement its methods. Give files and classes meaningful names.
- Follow the existing pattern in the codebase for the same job. Do not add a second way. If a better way exists, change every existing use, or propose that change first.
- Show a mockup and get engineer approval before adding a DocType or changing a DocType or dialog layout. Group related fields and avoid empty columns.
- Add a field description only when the label needs explanation. Keep it to one line and set `show_description_on_click`.
- Do not add a dependency when the standard library, Frappe or an existing dependency is sufficient.
- Do not commit secrets, tokens, `.env` content, or production credentials.

## Beacon rules

- Beacon only reads ClickHouse. It issues no DDL and no writes there. Datum owns the schema.
- A rule or SLI query error never resolves an alert. A failed fetch never removes a resource.
- Beacon never publishes an incident and never reopens a public one. A person decides what customers see.
- Write alerts only on transitions. A firing alert causes no writes.
- After each evaluation and each scheduled region run, every Firing, unsuppressed alert belongs to an open incident. `IncidentCoordinator` owns this.
- A field that drives a state machine is read-only in Desk. Only an action changes it, so every change runs the same checks.
- Status page methods are guest methods. They return only published data and cache each result for 60 seconds. An action that changes published data clears the keys it changes.
- All times are UTC.

## Code style

### All code

- Use complete words for names.
- Name packages for domain nouns, files for their objects, and methods for actions. Avoid process names such as `alerting` and `paging` for packages and files.
- Put business decisions on workflow objects. Keep queue entry points and transport adapters separate from business decisions.
- Put blank lines between logical code blocks in a function.
- Put behavior with the class or module that owns it.
- Extend an existing folder before you add a new one. Group related files and avoid crowded folders.
- Do not add generic `utils`, `helpers`, `common`, or similar folders.
- Do not add comments that repeat the code. Explain a business rule, invariant, external quirk, or concurrency rule only when needed.
- Do not add tests for now. The team adds the test suite after the first version is complete.

### Python

- Beacon is the only Frappe module. Prefix every DocType name with `Beacon `. Keep all DocTypes in `beacon/beacon/doctype/`.
- Keep domain behavior in `beacon/core/resource/`, `beacon/core/alert/`, `beacon/core/incident/` or `beacon/core/service/`. Keep app-wide behavior in `beacon/core/`.
- DocTypes own document state, permissions and public actions. Workflow objects own evaluation, synchronization, measurement and incident coordination.
- Keep queue configuration and entry points in `beacon/core/jobs.py`. Keep remote calls in client or channel adapter classes. Include the record and region in error logs when available.
- Put `@frappe.whitelist()` on the DocType method of each Desk or internal action. Forms call it with `frm.call`, and scripts call `POST /api/v2/document/<DocType>/<name>/method/<method>`. Do not add an `api/` function that loads a document and calls its method. Use `frappe.enqueue_doc` to run a document method as a job.
- Decorate an action that only writes to the database with `@document_action` (`core/transaction.py`). It retries the action after a lock conflict. An action that makes a remote call uses `@frappe.whitelist(methods=["POST"])`, because a retry could repeat the call.
- Give each action one method, for staff and for Beacon. Jobs run as Administrator, so the permission check passes. Do not add a second method that forwards to it.
- Add a function in `beacon/api/` only for a public viewing page, such as the status page, or for an external webhook, such as Twilio.
- Use the Frappe skills in `~/.claude/skills` (`syntax/`, `impl/`, `core/`, `errors/`) for Frappe patterns.
- Use type hints for public functions and important data structures.
- Raise specific exceptions. Handle only errors that the code can recover from.
- Avoid mutable global state, circular imports, and dependency injection that does not reduce coupling.
- Prefer small functions and clear object-oriented code when it fits the domain.
- Use `@property` only for a cheap, side-effect-free, no-argument operation that returns one noun-like value.
- Keep a method public when callers outside its owner use it. Prefix implementation-only methods with `_`. Use domain verbs instead of a generic `get_` name when they explain the operation.
- Name boolean properties and methods with `is_` or `has_`.
- Queries: `frappe.get_all` first, then `frappe.qb`. Raw `frappe.db.sql` only for what neither can express. No f-string SQL.
- Every scheduled or queued entry point runs as Administrator through `run_as_admin`. A `frappe.enqueue_doc` job queued from such an entry point runs as Administrator too.

### Front end

- The status page is a small Vue app in `frontend/`. It reads only the guest methods in `beacon/api/status.py`.
- Desk forms use plain Frappe form scripts. Put actions in the **Actions** group and destructive ones behind a confirmation.
- A read-only view in Desk, such as the Metrics and Log viewer Desk pages or the incident overview in a form, is a frappe-ui island in `frontend/src/islands/`. An island reads data and does no actions.

## Documentation

- Use ASD-STE100 Simplified Technical English. Use short, direct sentences and one term for one thing.
- Keep each Markdown prose paragraph on one source line. Do not use em dashes.
- Describe current behavior only.
- Write behavior once. Use `SPEC.md` for the code contract: key files, classes, extension points and invariants.
- Update the related documentation in the same change as a behavior or interface change.

## Validation and handover

- Mandatory: Before handover and before each commit, review every added and changed line for bugs, inconsistency with the surrounding code, and verbosity. Trim the change to the fewest lines that stay clear.
- Validate untrusted input at its boundary: Resource Source responses, rule query rows, Twilio callbacks and guest method arguments.
- Remove unnecessary helpers, wrappers, forwarding layers, generic maps, interfaces, and defensive fallbacks when they do not represent a real need.
- Before handover, run `ruff check` and `ruff format --check`, and `bench migrate` when DocTypes change.
- In the handover, report the result, changed paths, and verification.

## Commits and pull requests

- Use a short Conventional Commit subject: `type(scope): Sentence case`.
- Use one of `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, or `chore`. Use a behavior area as the scope: `resource`, `alert`, `incident`, `service`, `status`, `beacon`.
- Do not add an AI co-author, session data, or agent data.
- Plan the commit list first when a change needs more than one commit. Commit only when the user asks.
- Keep pull request descriptions short: Summary, Why, What changed.
