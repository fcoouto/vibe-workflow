---
name: workstreams
description: Coordinate suitable native child work, preserve its names, or manage independent resource-admission and capacity-recovery preferences in an enrolled checkout.
---

# Workstreams

Load only the route needed:

- **Naming:** read [naming rules](../../clients/workstreams-naming.md).
- **Status or enable/disable:** read [feature controls](references/procedure.md).
- **Ordinary delegation:** read [shared delegation safety](../../clients/delegation.md). It applies with either or both features off; no Workstreams revision is needed.
- **Heavy local work:** also read [resource admission](../../clients/workstreams-resources.md).
- **Recovery activation, monitor run or departure:** read [capacity recovery](../../clients/workstreams-recovery.md).
- **Managed child delivery:** read the [dispatch protocol](../../clients/workstreams-protocol.md).

New supported scopes default both features on only when no saved choice exists. Preserve each existing choice independently. Resource admission and capacity recovery have separate revisions and lifecycles; neither enables the other. For combined requests, handle controls first. Enabling a feature starts no worker or schedule. Preserve task ownership, budgets, naming and required review.
