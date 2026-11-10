# Code and architecture

Shape for the canonical architecture document, adapted through [onboarding](../../skills/onboard-my-project/references/procedure.md#adapt-project-documents). Omit inapplicable sections; never invent values. Preserve its established filename and capitalization.

## Actual stack

[Observed frameworks, runtime, package manager, persistence libraries/drivers and versions. Link lockfiles and record justified exceptions to accepted project decisions.]

## Code map

[Directories and files that exist, with responsibilities; distinguish proposed architecture from implemented behavior.]

## Boundaries and flow

[Actual presentation/input, validation, logic, data and external-service paths; ownership of environment, logging and connections. No empty layers or framework-specific architecture without a need.]

## Data and migrations

[Persistence or the reason for none. Where present: schema/types, migration destination and generation/review/application lifecycle, driver/runtime compatibility, connection/pool ownership, bounded queries and required transactions. Preserve authorized destinations.]

## Decisions

[Decision, reason, relevant alternative, source/date and revisit condition. Link product requirements rather than duplicating them.]

## Maintenance

[Update after relevant directory, dependency, contract or boundary changes; retire superseded decisions and label unverified behavior.]
