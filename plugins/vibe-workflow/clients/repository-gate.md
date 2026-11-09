# Repository merge assurance

Load this contract only when assessing repository merge assurance. It is a bounded offline fixture inspector; it does not replace [Merge Readiness](../policies/08-merge-readiness.md), [Code Review](../policies/06-code-review.md), [Security Review](../policies/04-security-review.md), or [Tests and Builds](../policies/05-tests-and-builds.md).

A [qualification task](runtime.md#staged-qualification) cannot acquire ordinary `RepositoryReadiness` or publish ordinary READY. A positive internal canary remains a bounded observation. Actual source acquisition remains closed until its independent qualification; fixture success or a retained selector cannot open it. Negative withdrawal keeps its existing operation owner.

Supply a UTF-8 JSON envelope on standard input to the installed package's CLI:

```sh
python3 -I -B /path/to/package/scripts/repository_gate.py < envelope.json
```

The envelope contains `policy`, `before`, `after`, and integer `now`. Use version 1 with runtime `schema_version: 2` pins, or version 2 with runtime `schema_version: 3` pins and `accepted_scope_digest`. Policy and observation versions must match the envelope; each nested pin object must use the matching runtime schema. Input is limited to 512 KiB. The CLI rejects malformed or ambiguous JSON and prints a bounded decision object. It always exits 1: no production publisher or evidence source is qualified.

`evidence_valid: true` means only that the supplied before/after records satisfy modeled checks and remain consistent. `source_authentication` remains `unverified`; `merge_authorized` and `published` remain false, with execution blockers. Caller-supplied records, fixture success, and this CLI cannot prove provenance, authorize a merge, or qualify repository enforcement. Do not weaken required D5/D6 coverage to make a fixture pass. See the [D7 validation record](../docs/d7-validation.md) for current qualification limits.
