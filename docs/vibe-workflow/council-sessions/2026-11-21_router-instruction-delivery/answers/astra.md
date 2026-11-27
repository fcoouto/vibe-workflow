D1: DROP — Go appends the entry to a tool result. That channel is excluded regardless of whether Haiku obeys it.  
D2: DROP — A successful `put` confirms storage, not trusted delivery. Stopping the push would remove Go’s working system instruction.  
D3: DROP — Dual delivery adds host calls and duplicate text for OpenAI. It also puts instructions in Go tool results.  
D4: KEEP — Check `event.model.providerID` each step. Use the entry for `claude-agent`; keep the working push for every other provider.  
D5: DROP — Keep the PR fallback note separate. No evidence shows hosts missing PR hooks support durable entries.  
D6: KEEP — Report the 2.3.2 hook ordering. Ask for a supported channel that reaches the first model call.  
D7: CHANGE: Revise #364 before merging — Its passing tests verify dual delivery, not correct placement across provider switches.

Compounding  
D4 preserves first-step delivery for OpenAI and Go without entry calls on sessions that stay there.  
On leaving `claude-agent`, resume pushing immediately. Test whether removing its entry sends a later “Disregard it” that conflicts with the push.  
Neither mechanism reaches a fresh `claude-agent` session’s first call. Without a working entry API, later calls remain uncovered too.

Top 3  
D4 — Match delivery to the provider.  
D1 — Treat tool-result placement as disqualifying; Go refusal itself was not measured.  
D6 — Seek a first-step fix upstream. Provider-switch behavior remains unverified.
