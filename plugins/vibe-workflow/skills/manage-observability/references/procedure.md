# Observability integration procedure

Configuration, enablement, credential and account operations require the current qualified canonical Codex scope and their existing prerequisites. Local status and disable instead verify the selected project's current physical identity and ownership of its saved private settings. Stale qualification, changed source digests or a missing, replaced or corrupt runtime database do not prevent viewing or disabling established consent. Preserve its historical bindings, expected revision and atomic ownership checks; do not repin, bootstrap or renew consent. A basic checkout without saved consent reports unconfigured. Local fixtures do not establish installed-client, native, OS-store or account qualification.

Use this for explicitly requested integration preparation, status, enablement, scope changes, disabling or dashboard access. It is independent of project enrollment. Routine coding, general Q&A and ordinary disabled tasks do not load this procedure or a provider reference.

An optional reference at the end of completed onboarding is only a follow-up proposal. It does not invoke this skill or authorize configuration, credential access, connection checks or collection. This workflow needs its own explicit user request; reuse matching authorization already supplied instead of asking again.

## Current capabilities and provider selection

The package provides optional local task-status preparation and checkout connection controls through the existing canonical owner. Controls store desired consent, select environment or explicit OS credentials, verify an exact Langfuse project and send one fixed synthetic metrics-only span only when authorized. There is no native collector, usage accounting or dashboard provisioning. Installing or invoking this skill activates none of these operations. Do not create alternative settings, a collector, provider registry or authority mechanism to fill gaps.

| Provider | Reference and current support |
| --- | --- |
| Langfuse | Read [Langfuse connection controls](langfuse.md) only when selected or its connection/forwarding is specifically discussed. Enablement and account access require the current qualified owner; native collection and dashboard qualification remain unavailable. |

Do not present other providers as available integrations. A future reviewed provider can add its own reference under this same skill; do not install a provider or change a destination merely because it was mentioned. A generic explanation or local capability-status request needs no provider reference.

## Resolve the requested operation and scope

Identify the operation, explicitly selected Project, authorized repositories and intended destination. Reuse canonical membership and existing owner facts where available. Do not enroll repositories, scan for more Projects, infer authorization from the current directory or treat dashboard filters as collection settings. Missing canonical identity or ownership is a prerequisite, not permission to create a new registry.

Reuse matching existing user authorization. Ask only for a material missing choice; never request credential values in chat. Preparing a proposal does not authorize external data transmission, account access, installation, payment or changed model routing. Preserve native model, reasoning, provider authentication, subscriptions and user customizations.

For real integration configuration or removal, apply [Tool Integrations](../../../policies/11-tool-integrations.md) through the supported configuration owner. Use machine preparation only for separately requested machine/client prerequisites; do not rerun full onboarding to change an optional integration.

## Handle requests within current support

- **Setup, enable or change scope:** prepare a concise, nonsecret proposal stating provider, destination, selected checkout, exported fields, source coverage, retention/access behavior and service limits. Apply the selected provider's Project-sharing check before linking a destination. Choose explicit OS storage for guided desktop setup or environment injection for headless use; preserve existing source choices. With matching authorization, configure the supported owner and verify the current project keys before storing desired opt-in. Explain that this permits an explicitly requested synthetic probe; native sources and their collector remain unavailable. Disable before changing a configured live destination or saved credentials. Do not represent checkout consent as Project-wide activation.
- **Status:** read the supported local owner without credentials or network. Report configured scope, desired opt-in and last verification time separately from current connection health (`not_checked`), unavailable collection and unknown independent forwarding. A missing, changed or unsupported authority is an error, not proof of disabled observability. Do not search secret stores, contact accounts, refresh task readiness or collect usage just to answer status. Load [task runtime](../../../clients/runtime.md) only when the user separately requests its supported local status/preparation operation; that operation can revalidate readiness and does not enable observability.
- **Disable:** use the supported owner without keys or network. A successful result stops future plugin probes after any current guarded operation; a busy operation returns an error and must not be called disabled. Preserve saved credentials. Report independent forwarding as unknown/unmanaged unless separately observed. Never claim remote data vanished. Do not delete remote history, dashboards or required local evidence as a substitute.
- **Dashboard access:** use an explicitly supplied or verified destination link within its access scope. Viewing does not authorize provisioning, account changes, collection, checks or model evaluations. If no verified link is available, report the prerequisite rather than inventing one.

For unsupported operations, finish with the prepared choices, precise missing capability and next owner/action. Do not retry through a separate SDK, alternate account, arbitrary configuration file or background job. Connection success, desired opt-in, active collection and qualified source coverage are distinct claims; each needs its own actual evidence.

## Local control contract

Run the installed package's `scripts/observability.py` with Python 3.11+ in an explicitly selected, already enrolled checkout. Configuration, enablement, probes and credential operations require a current qualified canonical Codex checkout. Status and disable use local settings ownership without requiring healthy runtime qualification. Project-wide consent and Windows runtime support remain unavailable. Native credential adapters do not qualify a Windows runtime. Do not bootstrap runtime state, adopt another checkout or edit enrollment to make an observability command succeed.

`python3 -I -B <plugin-root>/scripts/observability.py <action> --cwd <selected-checkout>` accepts one JSON object on stdin, at most 8 KiB. Use actual paths as separate quoted arguments. Do not pass credentials as arguments, input JSON or chat text.

| Action | Exact JSON input | Effect |
| --- | --- | --- |
| `status` | `{}` (or no input) | Read-only local status; creates no files and performs no connection check. |
| `configure` | `{"expected_revision":0,"destination":"https://cloud.langfuse.com","langfuse_project_id":"selected-project-id"}` | Store this authorized HTTPS origin/project, initially off. Use the actual current revision when reconfiguring a disabled connection. |
| `enable` | `{"expected_revision":1}` | Verify the current injected key pair against the exact configured project, then store desired opt-in. Sends no span and starts no collector. |
| `probe` | `{"expected_revision":2}` | Require desired opt-in, verify current keys again, send one fixed synthetic span and attempt bounded readback. No arbitrary task content is accepted. |
| `disable` | `{"expected_revision":2}` | Stop future plugin probes without credentials or network; preserve remote history and required local evidence. |

`configure` also accepts the optional nonsecret field `credential_source`, exactly `"environment"` or `"os-store"`. New guided desktop setup selects `"os-store"` explicitly; environment-only callers need no dependency. Omitting the field preserves existing version-2 selection and keeps new/legacy version-1 configurations in environment mode. Changing a source requires the disabled configuration; selecting OS storage never silently falls back to environment keys. Status reports the selected source without checking its secret entry or current backend health.

For selected OS storage, apply the provider's [Shared credential impact](langfuse.md#shared-credential-impact) before either command, including an already-linked connection update or removal. Then use the provider's credential procedure and these explicit local commands. They accept `--expected-revision N` instead of stdin JSON; never pass secret values as arguments or input JSON:

- `python3 -I -B <plugin-root>/scripts/observability.py store-credentials --cwd <selected-checkout> --expected-revision N` requires a foreground terminal with hidden input. It verifies the entered pair against the configured destination before one native-store write, advances revision and keeps consent off. It sends no span.
- `python3 -I -B <plugin-root>/scripts/observability.py forget-credentials --cwd <selected-checkout> --expected-revision N` explicitly removes the selected saved connection entry while disabled. It makes no Langfuse call and leaves the selected OS source intact; later lookup fails until keys are saved again.

Revisions in the table illustrate a new environment configuration. Read current status after each credential operation and use its exact revision; do not guess, bypass a conflict or overwrite changed state. A probe does not increment revision or retain a local measurement record. The owner stores only protected operational configuration under the existing private authority's `observability` directory. The existing scope ID, accepted-scope digest, qualification receipt and physical authority binding prevent consent reuse by another checkout or changed qualification; JSON labels cannot create authority. No secrets or probe history are stored there.

Handle failures without echoing private input, key values or remote bodies. A native-store or write/fsync failure can leave the requested change applied; local status alone does not inspect the saved keys. Report the uncertainty without automatic retries or speculative rollback of another writer's entry. A failed or delayed remote acknowledgement/readback must not be represented as verified. Use the selected provider reference for result meanings and credential prerequisites.

## Collection and evidence contract

Observability remains optional and disabled by default. When qualified native collection becomes available, the authorized scope covers actual workflow tasks and their coordinator, workers, reviews and verification. Current desired connection opt-in does not start that collection. Standalone Q&A outside those tasks is excluded; conversational turns inside a measured task still contribute usage. Resolve task scope from existing identities, not prompt contents.

Export only necessary numerical measurements, allowlisted outcomes/status metadata and safe opaque correlation IDs. Exclude prompts, completions, code, reviews, free-form diagnostics, paths, direct personal identifiers and secrets. Audit metadata and forwarded sources too. Local user-chosen identifiers and prepared reports are not automatically export-safe; opaque IDs do not establish anonymity.

Keep essential duration, verified outcomes, attempt identities, failures, retries/resumes and qualified usage/charges. Reuse existing correlation and metadata; omit low-value or duplicate step records only when complete accounting survives. Prefer a qualified inclusive total; otherwise require complete proven-disjoint source components. Reconcile retries, corrections, cumulative/incremental records and included worker/cache/reasoning subsets without double counting. Unknown measurements remain incomplete, never zero or invented estimates. Label provider-billed charges separately from price-derived estimates; subscription usage is not an API charge or avoided payment.

Apply authorized Project/Repository selection before ingestion. Dashboard filters only affect display. A source that cannot enforce scope stays inactive with a stated coverage gap. Start without sampling in the selected scope. Detailed metrics-only tracing is for qualification or explicitly requested investigation. Any later explicitly configured native sampling must select coherent tasks across sources, traces, workers and attempts; independent child samples do not prove complete task coverage. Qualification and baseline/candidate measurement runs require complete observations. Add no custom sampler, quota service, background collector, fallback analytics, HTML/offline report or duplicate measurement ledger. Use the backend's existing usage views; do not promise a fixed units-per-task budget or unmeasured savings.

Required local check, review, qualification, ownership, recovery and merge evidence remains under its existing owners and retention rules. Eligible metrics-only remote exports accept disclosed destination retention/access behavior without plugin deletion logic; this grants no content-export permission or exception to stricter user rules. Remote history cannot replace local proof, restore expired validation or grant readiness. Disabling stops future plugin activity through its owner and reconciles independent forwarding; it does not imply remote deletion. Outages, quotas, missing sources or credentials leave analytics incomplete without changing local safety decisions.

## Report completion honestly

State the operation, scope, observed result, unknown coverage and next action. Keep package/skill checks, actual client discovery, connection probes, source qualification and measured results separate. A verified synthetic probe proves that fixed record's write/readback, not native task observability, complete accounting or D9b/D11 acceptance. Preserve unresolved prerequisites without activating collection or changing an unrelated provider.
