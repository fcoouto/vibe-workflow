# Tool Integrations

Apply with the [shared contract rules](00-index.md).

**Conditional use.** Add optional integrations for a demonstrated project need. Required applicable catalog components retain their mandatory requirements under [Project Onboarding](10-project-onboarding.md); they cannot be declined as optional extras.

**Project Onboarding:** record selected integrations, purpose, supported clients/versions, configuration owner, permissions/authentication, prerequisites, validation and recovery. Record reasons for optional omissions.

| Element | Contract |
| --- | --- |
| Trigger | Selecting, adding, updating, repairing or removing an integration; using a new or unqualified capability; after changes or failures that invalidate prior qualification. |
| Decision | Reuse an existing capability, configure a compatible addition, defer an optional extra, or block a dependent mandatory capability. |
| Required action | Inspect actual support and existing configuration, resolve authority and prerequisites, make the smallest recoverable change, and verify discovery and a representative operation. |
| Evidence | Record client surface/version, component/source/version, owned configuration, required access, installed/configured/discovered/trusted/loaded/executed observations, actual probe results and limits. |
| Blocking condition | Required capability, access, trust or supported configuration is unavailable or unqualified. Optional extras may remain deferred; neither installation nor absence waives a mandatory requirement. |

## Choose and configure

For selected fresh Habblaud installations, discovered existing integrations and
authorized upgrades, load the [receiver and hook reconciliation procedure](../clients/worker-visibility.md#reconcile-on-discovery-and-upgrade).

- Discover installed plugins, skills, servers, hooks and CLIs before adding another. Reuse working authenticated integrations and shared project configuration without duplicating servers, interceptors or wrappers. A connector to an application does not prove support for model routing or worker dispatch.
- Check the current official format and actual client/version controls. Use version-appropriate technical documentation; use Context7 when available and official documentation/CLI help otherwise. Do not send secrets, private code or personal identifiers in queries. Do not copy another client's settings blindly or weaken permissions to make a probe pass.
- Inspect source, license, discovery metadata, portability and lockfiles for installed packages/skills. Keep configuration and install scope narrow. Do not migrate the application, add an SDK, switch bundlers/providers or create a service account merely to satisfy an optional capability.
- Preserve model/provider/authentication choices and subscriptions. Apply [Agent Delegation](03-agent-delegation.md) for model execution. New costs, account access, external data transmission, global changes and publishing need authorization covering those actions; an installed tool grants none of it.
- Keep credentials in supported secret/authentication storage outside project files and logs. Reuse approved OAuth or account connections where supported. Back up changed owned configuration, record how to undo it, and preserve unrelated settings. On removal, retire the owned registration and dependent references without deleting shared components or user data.

## Qualify the actual workflow

Test the prerequisites and one meaningful operation on each selected client surface; record each observation separately. Package installation, successful configuration parsing, client discovery and actual execution are different evidence. Use fresh-session loading where required; a new subagent is not proof that the client restarted.

Use local environments, synthetic data and disposable resources for browser/runtime probes. Do not reuse personal browser sessions or access production to establish setup success. External account/service queries must stay within approved access, destination and data scope. Dependency/graph analysis excludes secrets, vendor trees, caches and generated output where irrelevant; semantic analysis needs an authorized execution route and must not assume session authentication transfers to a standalone CLI.

Verify existing output/command behavior survives wrappers and hooks without duplicate execution. Confirm only supported events and responses; test denial, timeout or failure behavior before describing a control as enforcement. Missing accounts, restarts, trust, unsupported modes or manual steps remain explicit, with the action needed to resume.

An approved disposable [qualification attempt](../skills/onboard-my-project/references/qualification.md#staged-disposable-qualification) may exercise its exact pending mechanism within retained scope and budgets. Native frontend startup, API metadata and configured pass-through do not establish Toolkit task-guard enforcement. Keep actual CLI/Desktop provenance and observed effects separate; missing managed observations stay pending.

Treat release numbers as provenance, not eligibility. Determine support from the required live protocol, behavior and identity checks. Reuse evidence while its relevant capabilities, client surface, configuration, permissions and environment remain compatible; a release-number change alone does not invalidate it. Preserve schema validation, accepted source/candidate digests and historical receipts. Missing or changed required capabilities invalidate only affected claims; use [Workflow Maintenance](09-workflow-maintenance.md) for requalification and continue unrelated authorized work. Retain explicit dependency pins where qualified security-sensitive behavior still requires them.
