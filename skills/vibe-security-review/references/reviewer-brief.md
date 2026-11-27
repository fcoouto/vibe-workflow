# Reviewer brief

Fill this brief for a tier-3 reviewer or a full-review sweep. Dispatch with
`agent: "explore"`. Select the model via `smart_router_select` with `reviewing`
set to the author's model; without Smart Router, use the top tier on this session's own provider.
Never pick a pay-as-you-go route (for example the opencode provider) unless the user approved it.
Include the review scope, diff range and tier-3 triggers, or the full-review area
and bound. Wait for the reviewer's result; do not poll with shell commands.

The reviewer is read-only by instruction: shell access remains available. No
installs, builds, scanner runs or partner scripts unless the user approved that exact run;
being installed is not approval. Never use real credentials or contact live services.
The coordinator asks the user first and names each approved command in the brief;
the reviewer runs nothing that is not named there. Do not write or run exploit code.

Trace untrusted input to sensitive operations and inspect callers. For every
candidate, look for counterevidence such as upstream validation, ownership
checks and tests. Label it confirmed, likely or hardening; state whether it was
introduced or made worse by the change, or is pre-existing.

Reading a changed file is necessary and is not quoting. Never repeat a secret's
value in a reply, report, pull request, commit, log or any command you write.
Quote it as file, line and type only. Search for secrets only with commands that
list file names, such as `rg -l` or `git grep -l`; never use a search tool or
option that prints matching lines for a secret pattern.

## Partner skills

If `differential-review` appears in the coordinator's available skills, use it
within these limits for tier 3. Read its `adversarial.md` rather than delegating
to its plugin agent. The reviewer never writes files. The reviewer must skip or
redirect any report file a partner skill requests to its response, not a file,
and returns findings to the coordinator.
The coordinator writes the report using [report-storage.md](report-storage.md).
If no verified path exists, the reviewer writes nothing; the coordinator follows
the chat fallback in the storage procedure. Never use `fp-check`: its deep path
writes and runs exploit code.

An `explore` reviewer cannot load skills (`Permission denied: skill` was observed).
For each partner skill named in either mode, the coordinator loads it once and
takes its absolute folder from the skill tool's `Base directory for this skill:`
line. Put that path in the brief. The reviewer reads `<folder>/SKILL.md` with the
read tool and treats `{baseDir}` as that folder. The same applies to partner
skills named in a full-review sweep.
