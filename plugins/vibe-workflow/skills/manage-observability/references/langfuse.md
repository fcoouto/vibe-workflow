# Langfuse connection controls

Load only for selected Langfuse setup, connection checks, probes or forwarding questions. Follow the common procedure for authorization, payload scope and evidence; this reference adds provider-specific controls.

Configuration, enablement, key entry and account access require a current qualified canonical checkout. Local status and disable follow the common procedure's recovery-safe settings ownership checks. Fake local fixtures do not qualify installed clients, native OS stores or accounts.

## Select a destination

Identify the user's Langfuse Cloud region or self-hosted destination and intended Langfuse project. Recommend one dedicated Langfuse project per logical Vibe Project. Its repositories and worktrees normally share that destination and key pair; they are not separate Vibe Projects. Explicit sharing between distinct Vibe Projects remains allowed.

Before linking a second distinct Vibe Project to a known actively used exact origin/project, identify the existing Project use and ask for explicit sharing confirmation. Explain that destination-wide reports can combine their activity. Reuse matching explicit approval for that particular Project/destination; a general setup/OS-store approval or the policy allowing sharing is insufficient. If confirmation is declined, keep the link unchanged and select a dedicated destination instead.

Use existing authorized canonical Project/configuration ownership to establish identity, mappings and known active use. Do not infer another Vibe Project from a repository, worktree, local label or saved credential entry. Do not scan secret stores or add a registry. If mappings or activity cannot be verified, state the unknown and clarify whether the destination is in use and sharing is intended before linking; never assume unused. Current checkout-only controls do not automate canonical cross-Project discovery or enforce this confirmation globally. Apply this check in guided setup and report its limits honestly. Shared native collection still requires qualified source-side Project/Repository correlation and filters; separate destinations keep separate access boundaries and dashboard links.

Langfuse is the first planned connection provider. Native workflow collection and its exporter remain pending. A proposal alone does not authorize account access or a synthetic record. Reuse explicit matching setup/test authorization and the user's selected destination. Never paste credentials into chat, Git, a report or a destination URL.

Use an HTTPS origin without userinfo, path, query or fragment and the exact Langfuse project ID from the selected dashboard. EU Cloud uses `https://cloud.langfuse.com`; another region or self-hosted origin requires the user's actual destination. Do not substitute an account, follow redirects or provision a project. Configuration stores the selected nonsecret origin/project only, under the common procedure's existing private owner.

## Credentials and connection verification

The explicit `credential_source` chooses optional OS storage or the existing project-scoped `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` environment pair. Preserve existing environment configurations. OS mode never falls back to injected keys if the store is missing, locked, denied or malformed. A CLI login, `LANGFUSE_HOST`, SDK setup, MCP connection or previously verified pair is not a fallback. API keys belong to the selected Langfuse project, not to a workflow Repository.

The current single-pair connection/probe flow needs a project API key with **Full access**: it verifies the project, ingests a synthetic span and reads it back. **Ingestion** cannot read data; **Viewer** cannot ingest; **Scores ingestion** only submits scores. Separate ingestion and verification credentials are not implemented. Explain the current requirement when the user creates a key; do not present Full access as a requirement for a future export-only flow.

### Shared credential impact

Before either `store-credentials` or `forget-credentials`, identify known consumers of the exact saved origin/project through existing authorized Project/configuration ownership. Include repositories and worktrees of the same Vibe Project as well as distinct Projects. Saving replaces the pair used by all consumers at their next lookup; removal makes their next lookup fail. Neither operation revokes remote keys, stops in-flight requests or changes another checkout's consent.

Explain that impact and obtain authorization covering those consumers before prompting for keys or mutating the entry. Reuse matching explicit approval for that operation and shared scope. Destination-link sharing confirmation, general setup approval and the policy allowing sharing do not by themselves authorize a later credential replacement or removal. If sharing is unknown, state the unknown and clarify the affected scope before continuing. Do not inventory secret stores, scan other checkouts or add a registry.

### Local OS storage setup

Use the optional, pinned `keyring==25.7.0` dependency in the same chosen Python 3.11+ interpreter that runs the control command. The [optional requirements file](../../../requirements-observability.txt) covers macOS and Windows backend dependencies. Reuse a suitable managed interpreter or an explicitly chosen private virtual environment; do not install globally, downgrade unrelated packages or bootstrap a dependency automatically. The named dependency can be installed during authorized setup with `<chosen-python> -m pip install -r <plugin-root>/requirements-observability.txt`. Environment mode does not need it.

The adapter instantiates only macOS Keychain or Windows Credential Manager directly. It never selects a configured/default/chainer/plaintext backend. `KEYRING_PROPERTY_*` redirection, including names containing that marker, and `KEYCHAIN_PATH` are rejected; the helper does not change those user settings. Unsupported or unqualified versions are prerequisites, not reasons to try another store. The pinned Windows path reads, updates and removes only its exact managed target, without compound-entry migration or fallback; a different stored username is a conflict. Actual pinned-backend tests replace native calls only; they do not establish OS access. The current workflow CLI remains POSIX-only and does not establish Windows activation.

After the destination-sharing check, configure the disabled selected checkout with `credential_source:"os-store"` through the common control. Apply [Shared credential impact](#shared-credential-impact), then ask the user to run the supported `store-credentials` command in their own foreground terminal with the exact current revision. Both values use hidden input. Noninteractive entry and unsafe no-echo fallback are refused. Keep secrets out of chat, command arguments, stdin JSON, shell history, committed files, reports and model-worker context. The OS may ask permission for the chosen Python executable; preserve its access controls. macOS access can extend to other code using that same Python executable, so this is not plugin-specific isolation.

The helper checks the current owner before entry, rechecks after entry, verifies the pair against the exact configured Langfuse project, and saves one bounded native-store blob. A store operation never enables consent or sends a trace. `enable` remains a separate step using the new current revision; a synthetic probe needs its own existing or explicit authorization. The native entry is shared by the exact origin/Langfuse project, while each checkout retains separate opt-in. No secret or probe history enters local configuration.

Disable preserves the saved pair. `forget-credentials` explicitly removes that connection's saved entry while disabled, under [Shared credential impact](#shared-credential-impact). It does not erase history or alter unrelated secrets. Errors can leave a native write/removal uncertain; do not retry or roll it back automatically. Real OS access and real-account verification need their own observations; fake-backed tests and terminal proof do not establish them.

Each live operation uses its current selected-source pair for `GET /api/public/projects` and requires exactly the selected project. Recheck the held owner after a native lookup, immediately before the sole ingestion POST and after the operation. `enable` stores desired connection opt-in after verification; it sends no trace and starts no native collector. Local status reports the source and cached verification timestamp without querying the store or current remote health. Changed/expired keys must pass a new check before probe ingestion. Status and disable need no keys, store or network.

## Fixed synthetic probe

Run `probe` only when the user has authorized a real synthetic record and the exact current checkout configuration is opted in. It writes one complete fixed OTLP span to `/api/public/otel/v1/traces` with JSON, Basic project-key authentication and ingestion version `4`. The span contains only fixed service/type/name markers, fresh opaque trace/span IDs, timestamps and explicit synthetic metadata. It contains no actual workflow identifiers, prompts, completions, source, paths, tokens or costs. It consumes destination quota. It does not configure Broadcast, MCP, global client settings or SDK auto-instrumentation.

The helper queries only the generated trace through Observations API v2, with short request/readback budgets and at most five reads. It checks the exact trace, observation, selected project, fixed name/type and metadata, with absent input/output and user/session content. An ingestion partial rejection, wrong project, redirect or mismatched/content-bearing record cannot report verified success. Remote bodies and credentials never enter the result.

`accepted:true, verified:true` proves only this synthetic write/readback. `accepted:true, verified:false, status:"pending"` means ingestion acknowledged the record but readback did not establish the required proof. `ingestion_outcome_unknown` means the write may have arrived despite a missing/failed acknowledgement. Never retry ingestion automatically or manufacture token/cost zeros; usage and charges stay unknown. A later explicit probe creates another record, so do not rerun it to hide an incomplete result.

Do not assume an installed SDK, account connection or synthetic probe qualifies the actual workflow. Native completion needs explicit canonical Project opt-in, authorized payload/destination handling and qualified source collection; the existing source qualification order, including prior repository-gate proof for D9b and a real native-only coordinator/worker/reviewer measurement case, remains intact. Use Context7 or official version-specific documentation for requested API troubleshooting or authoring changes, not routine disabled tasks.

## Source coverage and forwarding

OpenRouter Broadcast observes OpenRouter-routed requests only. Native coordinator/worker and local-model measurements require their own qualified sources; do not reroute them for visibility. Qualify the actual connector's request identity and correlation fields rather than inferring support from API documentation or matching by timing.

Broadcast destination filtering, sampling and privacy settings are independent of Langfuse SDK controls. Verify authorized source-side scope and the actual exported metadata; a privacy-mode checkbox alone does not prove the payload is safe. Do not enable account-wide forwarding to bypass missing task/Repository filtering. Report any separately configured forwarding that remains active when plugin collection is disabled.

## Retention, limits and dashboards

Langfuse Cloud Hobby can be selected within its current supported limits. Check current plan limits only for requested setup or explicit troubleshooting, not routine coding. A history-access window does not delete older data. The user accepts disclosed destination retention for eligible metrics-only exports; do not add plugin deletion logic or promise remote erasure. Required local evidence keeps its existing rules independently of remote history.

Reuse Langfuse's existing dashboards and Usage Management view. No custom reporting application, offline snapshot or unit counter is needed. Dashboard access does not authorize provisioning or overwriting user customizations. Supported filters and widgets need target-version qualification; missing history or source support remains explicit. Dashboard qualification remains the last deliverable and is not a prerequisite for D9b source acceptance. The connection controls and synthetic probe qualify none of those native views.
