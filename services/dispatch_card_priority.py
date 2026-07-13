from __future__ import annotations

from datetime import date, timedelta

_FAR_FUTURE = "9999-99-99"


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def card_priority_rank(card: dict, *, today: date | None = None) -> int:
    """Lower rank sorts first.

    Priority order: severe exception, late appointment/deadline, LFD/cutoff
    today, appointment due soon (within 1 day), unassigned Ready to
    Dispatch, everything else.
    """
    reference_today = today or date.today()
    need_date = _parse_date(card.get("earliest_need_date", ""))
    lfd = _parse_date(card.get("earliest_lfd", ""))

    if card.get("exception_count", 0) > 0:
        return 0
    if need_date is not None and need_date < reference_today:
        return 1
    if lfd is not None and lfd <= reference_today:
        return 2
    if need_date is not None and need_date <= reference_today + timedelta(days=1):
        return 3
    if card.get("canonical_status") == "Ready to Dispatch" and card.get("unassigned_count", 0) > 0:
        return 4
    return 5


def sort_booking_cards(cards: list[dict], *, today: date | None = None) -> list[dict]:
    """Sort booking cards by priority rank, then earliest appointment date
    (missing dates sort last), then booking number as a stable, deterministic
    final tiebreaker."""

    def sort_key(card: dict):
        return (
            card_priority_rank(card, today=today),
            card.get("earliest_need_date") or _FAR_FUTURE,
            str(card.get("booking_number", "")),
        )

    return sorted(cards, key=sort_key)
