# Routing: no officer covers the case's area

Proposed change to `oan_grievance_service/services/routing.py` (`apply_routing`, `pick_officer_by_strategy`).

## Current behaviour

Area is a ranking, not a filter. `ranked_assignments` keeps every desk whose category, type and provider scopes match (an empty scope is a wildcard) and sorts by area fit, then specificity, then creation. A desk whose officers do not cover the case's area scores `UNBOUNDED` and sorts last, but is not dropped.

`pick_officer_by_strategy(desk, case)` walks role-level rungs from lowest to highest. Within a rung, `_covering_case` keeps officers whose area contains the case's area, narrowest first.

- **Kebele has no officer, Woreda does:** the Woreda-rung officer covers the Kebele case, so the case already goes up. No change needed.
- **No officer on the desk covers the area at any rung** (for example, the case is in another Woreda): `apply_routing` line 434 retries `pick_officer_by_strategy(candidate)` with no case. That picks the lowest rung first, so the case can land on a Kebele officer from an unrelated area. `routing_rule` and `routed_automatically=1` are set as normal, so nothing records that the area did not fit.

Also: area span is the first sort key, so a generic desk (no category) that covers the area outranks a category-specific desk that does not.

## Proposed change

1. When the best desk has no officer covering the case's area, do not fall back to the lowest rung of the wrong area.
2. Send the case to the Unrouted desk instead (or the Woreda head desk, once it exists, see CR-21).
3. Record the reason in the audit trail (`audit.record_version` / lifecycle reason), for example `No officer on desk <name> covers area <area>; sent to triage`.
4. Review the sort order in `ranked_assignments`: a category-specific desk should rank ahead of a generic desk even when its area fit is wider.

## Open decision

Fallback target when no officer covers the area:

- **Unrouted desk (preferred):** a person triages it and the reason is visible.
- **Highest rung on the matching desk:** a Zone or Region officer may still be nowhere near the case.

## Code touched

- `services/routing.py`: `apply_routing` (the `or pick_officer_by_strategy(candidate)` fallback), `ranked_assignments` sort key.
- `tests/test_routing*.py` (or the existing routing suite): case in a Woreda with no covering officer; category desk vs generic desk ordering.
