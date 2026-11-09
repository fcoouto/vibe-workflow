# Evidence plan

Apply the [common evidence contract](evidence.md) before this procedure.

The examples show the required version-1 request fields. Apply these constraints:

- Use 1–32 criteria, at most 32 checks and a `review_limit` of 1–8. Empty checks require behavior criteria.
- Commands require exact argv, relative `cwd` and dependencies, and a timeout of 1–300 seconds. The credential-free environment requires `PATH`; only `LANG`, `LC_ALL` and `TZ` may be added. Effects are `read-only` or `disposable`; all four effect flags must remain false.
- Review applicability is `required`, `inapplicable` or `unresolved`; security impact is `assessed` or `unresolved`. Comparison is `endpoint` or `merge-base`.
- Instruction paths must be absolute and safe. Exclusions use `{"path":"relative/path","impact":"scoped reason"}`. Optional `relevant_untracked` lists relevant ignored paths.
- Optional check `thresholds` accepts only `{"exit_codes":[0]}`. Mutable/disposable checks require `resources` using [resource claim fields](resource-claims.md). Read-only checks may derive read claims from dependencies; empty or unknown footprints serialize affected checkout work. Never infer mutable-resource isolation.
- Baseline status is `passed`, `failed` or `not-run`. A comparable failed baseline may include integer `exit_code` and SHA-256 `output_digest`; only an exact signature match is pre-existing. Otherwise failure comparison remains unresolved.
- Changing accepted rules requires `change_decision_ref`.

## Minimal plan request

Combine this `plan` object with the current identity and revision fields in the
[common request envelope](evidence.md#request-fields). Replace every placeholder
with the accepted task decision, actual base commit and scoped reasons or
references; the example supplies no permission or observed result.

<!-- example:evidence-plan-minimal -->
```json
{
  "plan": {
    "version": 1,
    "decision_ref": "<ACCEPTED_DECISION_REF>",
    "criteria": [{"id": "acceptance", "checks": [], "behavior": true}],
    "checks": [],
    "reviews": {
      "general": {
        "applicability": "required",
        "reason": "<GENERAL_REVIEW_REASON>",
        "independent": true
      },
      "security": {
        "applicability": "required",
        "reason": "<SECURITY_REVIEW_REASON>",
        "independent": true
      }
    },
    "security_impact": {
      "status": "unresolved",
      "reason": "<SECURITY_IMPACT_REASON>",
      "reference": "<SECURITY_IMPACT_REFERENCE>"
    },
    "base": "<BASE_COMMIT>",
    "comparison": "endpoint",
    "nontrivial": true,
    "instructions": [],
    "exclusions": [],
    "review_limit": 1
  }
}
```

Behavior-only acceptance still needs later observed proof. Unresolved security
impact prevents completion. Empty instruction and exclusion arrays are
illustrative and valid only for their accepted scope; include applicable inputs
and justified exclusions for the real task. API acceptance of a plan proves
neither passed checks nor authorization to execute them.

## Read-only check component

For a plan with an accepted read-only check, put this object in `plan.checks` and
set `plan.criteria` to `[{"id":"acceptance","checks":["unit"],"behavior":false}]`.
Replace `<ACCEPTED_ARGV>` with the real approved argv **array**, not a literal
executable string. Use the accepted credential-free `PATH`, command inspection
reference and baseline record. This example declares a dependency on `app.py`;
adapt that path to the inspected command's actual footprint.

<!-- example:evidence-plan-read-only-check -->
```json
{
  "id": "unit",
  "argv": "<ACCEPTED_ARGV>",
  "cwd": ".",
  "timeout": 3,
  "env": {"PATH": "<CHECK_PATH>"},
  "effects": {
    "kind": "read-only",
    "inspection_ref": "<INSPECTION_REFERENCE>",
    "network": false,
    "production": false,
    "publish": false,
    "migrations": false
  },
  "baseline": {"status": "not-run", "reference": "<BASELINE_REFERENCE>"},
  "dependencies": ["app.py"],
  "thresholds": {"exit_codes": [0]}
}
```

Accepting this definition does not run the command or its baseline. Keep the
baseline `not-run` until actual execution establishes another result.
