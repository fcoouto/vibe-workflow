"""Coverage-row outcome rules for onboarding plans; findings become plan blockers."""

USER_DECISIONS = ("confirmed", "inherited")


def coverage_problem(row, evidence, decisions):
    """Return why a catalog coverage row cannot stand as recorded, or None."""
    if not row["reason"]:
        return "Applicability/optional omission needs an explicit reason"
    outcome = row["outcome"]
    if outcome == "omit":
        if row["mandatory"]:
            return "Mandatory adoption cannot be omitted by runtime inapplicability"
        if row["applicability"] == "applicable" and not any(
                decisions[ref]["disposition"] in USER_DECISIONS and decisions[ref]["rule"] == row["id"]
                for ref in row["decision_ids"]):
            return "Applicable component cannot be omitted without a linked user decision for it"
    elif outcome == "planned" and not row["actions"]:
        return "Planned adoption lacks exact proposed actions"
    elif outcome == "blocked":
        if not (row["blocker"] and row["resumption"]):
            return "Blocked component needs a prerequisite and a resume condition"
        return row["blocker"]
    elif outcome == "reuse" and (row["current_adoption"] != "qualified" or not any(
            evidence[ref]["kind"] == "behavior" and evidence[ref]["inputs"] for ref in row["qualification_evidence"])):
        return "Equivalent integration lacks observed behavioral evidence on pinned inputs"
    if row["mandatory"] and any(decisions[ref]["conflict"] and decisions[ref]["mode"] == "preserve"
                                for ref in row["decision_ids"]):
        return "Preserved conflicting behavior blocks mandatory adoption"
    return None
