D1: DROP — On `opencode-go`, the entry is appended to a tool result. Settled rules forbid that channel. The entry also misses every session's first step.

D2: DROP — `opencode-go` would then rely only on the tool-result channel. It also adds "is the entry in effect" state, so it is not simpler.

D3: CHANGE: keep the every-step push for all providers, and put the entry only for `claude-agent` sessions (see D4) — As written, PR #364 already sends the entry to `opencode-go` as a tool-result append. It also makes `openai` see the instruction twice. Both cost tokens and break the lightweight rule.

D4: CHANGE: gate only the put. Use `if (claude) await entry(sessionID, true); else if (entries.get(sessionID) === true) await entry(sessionID, false)` with `claude = event?.model?.providerID === "claude-agent"`, and keep the push unconditional — This is one comparison and reuses `entry()`. A session that switches models self-corrects at its next step. The push stays the fallback for any unknown provider. Check that `event.model.providerID` exists in the 2.3.2 `context` event before relying on it. I did not verify it; the brief only asserts it.

D5: DROP — Only hosts that lack plugin tool hooks show this note. Those hosts likely lack the entry API too, so moving it adds code for nothing.

D6: KEEP — It costs little, and it is the only real fix for the first-step gap. Ask upstream to run plugin `context` hooks before the `claude-agent` copy, or to offer a supported channel. Filing the issue is outward-facing, so get the user's OK first.

D7: CHANGE: add the D3/D4 gate to PR #364 before merging, not in a follow-up — It changes the same lines, so a follow-up is churn. Merging now would ship the tool-result placement on `opencode-go`. That is a positive bias to flag: 207 passing tests and 3 reviews never covered how `opencode-go` places the entry.

**Compounding**
- D3 and D4 work as one change: push for everyone, put the entry only for `claude-agent`. Together they rule out D1 and D2, because a single mechanism would force the tool-result channel on `opencode-go`.
- My D3/D4 call assumes Claude-family models on `opencode-go` would treat the appended entry as injection. That is inferred from the `claude-agent` spike, not measured. Haiku might accept a host-formatted `<context>` block. Even so, the system push already reaches that provider, so the entry adds nothing there.
- No option closes the first-step gap on `claude-agent`. Only D6 can, so delivery stays best-effort from step 2 until upstream changes.
- Gating the put on provider makes D7's "merge now" cheap: the extra diff is about 3 lines plus a test.

**Top 3**
1. D4: gate the put to `claude-agent` and keep the push for all.
2. D7: put that gate into PR #364 before merging.
3. D6: report the hook ordering upstream, after the user approves.
