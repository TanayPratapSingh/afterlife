"""What state is a paper in, given everything that has happened to it?

A paper can collect several editorial notices over its life. In the
Retraction Watch snapshot of 2026-10-16, 2,534 DOIs appear in more than one
record: an expression of concern followed by a retraction, a correction and
later a retraction, a retraction and then a reinstatement. Any registry that
keeps one row per paper has to collapse that history, and the collapse is a
policy decision, so it lives here, in one place, with tests.

The policy, applied to events in date order:

* Retraction sets RETRACTED. Nothing but a later reinstatement undoes it.
* Reinstatement after a retraction or a concern sets REINSTATED.
* Expression of concern sets CONCERN unless the paper is already RETRACTED.
* Correction sets CORRECTED only if the paper is otherwise in good standing.
  A correction to a retracted paper does not make it citable.

Events on the same day are ordered by severity, so a retraction and a
correction issued together leave the paper RETRACTED.

The Spark silver job computes the same answer with a closed form expression
over per paper maxima (no Python UDF, no collect_list). Tests check that the
fold below and that expression agree on every DOI in the landed data.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from enum import StrEnum


class Status(StrEnum):
    OK = "ok"
    CORRECTED = "corrected"
    CONCERN = "concern"
    RETRACTED = "retracted"
    REINSTATED = "reinstated"


class Nature(StrEnum):
    RETRACTION = "Retraction"
    CONCERN = "Expression of concern"
    CORRECTION = "Correction"
    REINSTATEMENT = "Reinstatement"


# Tie break for events on the same date: the milder event is applied first,
# so the more severe one wins. Reinstatement is applied last on its date, on
# the reading that a same day reinstatement answers the notice it follows.
_SAME_DAY_ORDER = {
    Nature.CORRECTION: 0,
    Nature.CONCERN: 1,
    Nature.RETRACTION: 2,
    Nature.REINSTATEMENT: 3,
}

# Citable means: may be used as supporting evidence by a downstream system.
CITABLE = {Status.OK, Status.CORRECTED, Status.REINSTATED}


def parse_nature(value: str | None) -> Nature | None:
    if not value:
        return None
    v = value.strip().lower()
    for n in Nature:
        if n.value.lower() == v:
            return n
    return None


def fold(events: Iterable[tuple[Nature, date | None]]) -> Status:
    """Collapse a paper's notice history into one status.

    Events with no date sort before dated events: an undated notice is known
    to have happened but not when, so it cannot override a dated one.
    """
    ordered = sorted(
        events,
        key=lambda e: (e[1] is not None, e[1] or date.min, _SAME_DAY_ORDER[e[0]]),
    )
    state = Status.OK
    for nature, _ in ordered:
        if nature is Nature.RETRACTION:
            state = Status.RETRACTED
        elif nature is Nature.REINSTATEMENT:
            if state in {Status.RETRACTED, Status.CONCERN}:
                state = Status.REINSTATED
        elif nature is Nature.CONCERN:
            if state is not Status.RETRACTED:
                state = Status.CONCERN
        elif nature is Nature.CORRECTION:
            if state in {Status.OK, Status.CORRECTED, Status.REINSTATED}:
                state = Status.CORRECTED if state is Status.OK else state
    return state


def order_key(nature: Nature, when: date | None) -> tuple[bool, date, int]:
    """The sort key the fold uses. Keys of different natures never tie."""
    return (when is not None, when or date.min, _SAME_DAY_ORDER[nature])


def closed_form(
    last_retraction: tuple | None,
    last_concern: tuple | None,
    last_reinstatement: tuple | None,
    has_correction: bool,
) -> Status:
    """The fold, rewritten over per paper maxima of `order_key`.

    This is the shape the Spark job can compute with one grouped aggregation
    and a CASE expression. Each branch is the fold's answer given which kind
    of major event (retraction, concern, reinstatement) came last.
    """
    majors = [k for k in (last_retraction, last_concern, last_reinstatement) if k is not None]
    if not majors:
        return Status.CORRECTED if has_correction else Status.OK
    last = max(majors)
    if last == last_retraction:
        return Status.RETRACTED
    if last == last_concern:
        reinstated_after_retraction = last_reinstatement is not None and (
            last_retraction is None or last_reinstatement > last_retraction
        )
        if last_retraction is not None and not reinstated_after_retraction:
            return Status.RETRACTED
        return Status.CONCERN
    if last_retraction is not None or last_concern is not None:
        return Status.REINSTATED
    return Status.CORRECTED if has_correction else Status.OK


def to_evidenceops(status: Status) -> str:
    """Map onto EvidenceOps' TrustStatus vocabulary (ok, corrected,
    concerned, retracted). A reinstated paper is citable there as ok."""
    return {
        Status.OK: "ok",
        Status.CORRECTED: "corrected",
        Status.CONCERN: "concerned",
        Status.RETRACTED: "retracted",
        Status.REINSTATED: "ok",
    }[status]
