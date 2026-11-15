---
name: keep-it-simple
description: Use when implementing a feature or fix and tempted to add abstraction, configuration, options, or flexibility beyond what was actually asked for. Makes the agent write the minimum code that solves the real problem, instead of speculative complexity built for imagined future needs.
---

# Keep It Simple

Write the **minimum code that solves the actual problem.** Speculative complexity is a cost you pay now for a future that usually never arrives.

## Defaults

- **Don't build for requirements you don't have.** No config options, plugin systems, or "flexibility" nobody asked for. Solve today's problem; generalize later *if* a second case actually shows up.
- **No abstraction for single use.** Don't extract an interface, a base class, or a factory for something used once. Two concrete cases earn an abstraction; one does not.
- **Don't handle impossible cases.** Validate real inputs; don't write error handling for states that can't occur in this code path.
- **Prefer the boring solution.** A plain function beats a clever pattern when both work. Clever is a tax every future reader pays.

## The test

Before adding a layer, ask:

> Would a senior engineer look at this and say it's *overcomplicated*?

If yes, delete the cleverness and write the direct version.

## When complexity IS warranted

Real, present requirements justify real structure — genuine duplication, a known second use case, an actual extensibility need someone asked for. The rule isn't "never abstract." It's "don't abstract *speculatively*." Earn each layer with a concrete reason.

## Why this matters

Every speculative abstraction is code to read, test, debug, and maintain — in exchange for flexibility you may never use. The simplest version that works is almost always the right one, and it's far easier to add structure later than to remove it.
