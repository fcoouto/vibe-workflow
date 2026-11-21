D1: DROP — on `opencode-go` the entry lands in a tool result, a channel the settled rules exclude; it also misses step 1 on every provider.
D2: DROP — once the push stops, `opencode-go` has only the tool-result copy; the fallback push never reaches `claude-agent`, so it does not cover step 1 there.
D3: CHANGE: keep the push on every step, but put the entry only for `claude-agent` sessions — the brief's "if wrong" flatters D3: on `opencode-go` it also places the instruction in a tool result.
D4: CHANGE: keep the push unconditional and gate only `entry(sessionID, true)` on the provider; put when the provider is unknown; never remove on a model switch — one condition, no second path.
D5: DROP — hosts without tool hooks likely lack the entry API too; this adds code for a rare note.
D6: KEEP — one issue is cheap; an upstream fix would let you delete `entry()`, `drop()`, the `entries` map and the unload cleanup.
D7: CHANGE: add the provider gate to #364 before merge — it is one condition plus one test on the same lines.

Compounding:
- D3 and D4 converge on one design: the push is primary and stateless, and the entry is a `claude-agent`-only supplement. This reverses the user's "injection as fallback" idea, so D1 and D2 fall together.
- One mechanism for all three is not possible today: the push fails on `claude-agent`, and the entry is misplaced on `opencode-go`. Only D6 can deliver a true single mechanism.
- The gate also serves the lightweight rule: no host call, map slot or unload remove for `openai` and `opencode-go` sessions. The existing "off" branch still removes stale entries from any session.
- D7 depends on D4's one unverified fact (below). If the field is absent, the gated code behaves exactly like #364 as it stands, so the gate cannot block the merge.

Bias flags:
- Optimistic: no item fixes step 1 on `claude-agent`. A delegation in the first model call skips the router under every option.
- Optimistic: `event.model.providerID` in the `context` hook is not recorded anywhere in this repository. Tests pass only `{ system, sessionID, options }`. One live check is needed.
- Optimistic: the evidence is one model per provider with few runs. "Sonnet treats it as binding" is not proven for Opus, Fable or Haiku through `claude-agent`.
- Pessimistic: the `opencode-go` refusal is extrapolated. The spike measured plugin-appended text through `claude-agent`, not the host's `<context key>` block on the Go plan, and Go also serves non-Claude models. This does not change the verdicts, because the push already works there.
- Pessimistic: D4's "future provider missed" risk is small. The push still covers every provider that reads the system prompt, and the gap is easy to see in one prompt.

Top 3: D4, D7, D6
