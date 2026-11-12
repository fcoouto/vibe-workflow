"""The canary's command-line options for `opencode_live.py` (moved here unchanged to keep that entry under its line
limit), and the ranked-only branch (F7 correction F4): `--ranked-preflight` prints the pins, coverage, baseline,
earliest reset and the digest to approve, from saved local data with 0 calls and no server; `--ranked` runs 60 to
80 ranked admissions on the production ledger with no explicit choice or `--route` on any admission, under its own
`ranked-canary` call purpose. That purpose is 0 in code: the run is refused, before anything starts, until a future
recorded and approved cap line opens enough of it; the pilot's calls never fund it. The plugin-chain probes still
need one fixed approved route, named separately as `--probe-route` and never used for an admission.
"""
import time

from tests.qualification import canary, canary_ranked, canary_ranked_state, safety


def add_arguments(parser):
    parser.add_argument("--project", help="canary: the allowlisted checkout")
    parser.add_argument("--canary-state", help="canary: the dedicated routing state directory (default %s; never the "
                                               "production one)" % canary.DEFAULT_STATE)
    parser.add_argument("--production-ledger", action="store_true", help="canary and its export only, with --approval: "
                        "the production routing settings and ledger (never the default)")
    parser.add_argument("--reset-canary-state", action="store_true",
                        help="delete the dedicated canary state (refused while a harness process is alive)")
    parser.add_argument("--admissions", type=int, default=None, help="canary: admissions (1 to the pilot max)")
    parser.add_argument("--canary-allow", help="canary: this run's allowlisted checkout (on the canary managed_projects)")
    parser.add_argument("--pilot-max", type=int, help="canary: this run's admission cap (bounded by the pilot ledger cap)")
    parser.add_argument("--export-canary-admissions", action="store_true", help="0 calls: export the canary admissions")
    parser.add_argument("--replay-dir", help="canary: the admission export dir (default under ~/.local/state)")
    parser.add_argument("--ranked", action="store_true", help="canary on the production ledger: ranked admissions")
    parser.add_argument("--ranked-preflight", action="store_true", help="0 calls: the ranked pins and digest")
    parser.add_argument("--approve-digest", help="ranked: the approved --ranked-preflight digest")
    parser.add_argument("--acknowledge-low-coverage", type=int, metavar="N",
                        help="ranked: the computed minimum full-reservation coverage, when below 60")
    parser.add_argument("--ranked-state-dir", help="ranked: the owner-only run record dir (default %s)"
                        % canary_ranked_state.default_dir())
    parser.add_argument("--probe-route", help="ranked: the fixed approved route of the plugin-chain probes only")


def exposure(n):
    """Calls a ranked run may reserve at most: the pilot driver's write-ahead rule (two probes, the first admission
    and its title, one per further admission): 83 for the 80-admission stop."""
    return canary.planned_exposure(n)


def preflight(args):
    """`--ranked-preflight`: the record to approve and its digest. Reads the production settings' saved local data
    only; no server, provider read, model call or write."""
    state = canary.resolve_state(None, True, args.approval)
    project = canary.check_project(args.project, args.canary_allow, state["settings"], managed=True)
    record = dict(canary_ranked.preflight(state["settings"], [safety.HARNESS_NAME], int(time.time()),
                                          args.acknowledge_low_coverage), project=project, ledger=str(state["ledger"]))
    return dict(record, digest=canary_ranked_state.digest(record))


def check(args, ledger):
    """The ranked run's refusals before anything starts; returns its admissions and the probe route."""
    if not (args.canary and args.production_ledger):
        raise safety.Refused("--ranked belongs to --canary --production-ledger")
    if args.route or args.pilot_max is not None:
        raise safety.Refused("ranked admissions carry no --route or --pilot-max; name the probes' route as --probe-route")
    if not args.approve_digest:
        raise safety.Refused("--ranked needs --approve-digest from --ranked-preflight")
    n = canary.RANKED_MIN if args.admissions is None else args.admissions
    if type(n) is not int or not canary.RANKED_MIN <= n <= canary.RANKED_MAX:
        raise safety.Refused("the ranked canary runs %d to %d admissions" % (canary.RANKED_MIN, canary.RANKED_MAX))
    if ledger.remaining(canary.RANKED_PURPOSE) < exposure(n):
        raise safety.Refused("the %s call purpose has %d of the %d calls this run needs; it opens only through a "
                             "recorded, approved cap line" % (canary.RANKED_PURPOSE,
                                                              ledger.remaining(canary.RANKED_PURPOSE), exposure(n)))
    route = safety.parse_route(args.probe_route)
    canary.plan_assignments(n, "check", ranked=True)
    return n, route
