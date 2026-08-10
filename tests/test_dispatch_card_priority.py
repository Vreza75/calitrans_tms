from datetime import date

from services.dispatch_card_priority import card_priority_rank, sort_booking_cards


def _card(booking="MAEU-1", status="Ready to Dispatch", exceptions=0, need_date="", lfd="", unassigned=0):
    return {
        "booking_number": booking,
        "canonical_status": status,
        "exception_count": exceptions,
        "earliest_need_date": need_date,
        "earliest_lfd": lfd,
        "unassigned_count": unassigned,
    }


TODAY = date(2026, 7, 13)


def test_severe_exception_ranks_first():
    card = _card(exceptions=2)
    assert card_priority_rank(card, today=TODAY) == 0


def test_late_appointment_ranks_before_lfd_today():
    late_card = _card(need_date="2026-07-10")
    assert card_priority_rank(late_card, today=TODAY) == 1


def test_lfd_today_ranks_third():
    card = _card(lfd="2026-07-13")
    assert card_priority_rank(card, today=TODAY) == 2


def test_appointment_due_soon_ranks_fourth():
    card = _card(need_date="2026-07-14")
    assert card_priority_rank(card, today=TODAY) == 3


def test_unassigned_ready_to_dispatch_ranks_fifth():
    card = _card(status="Ready to Dispatch", unassigned=1)
    assert card_priority_rank(card, today=TODAY) == 4


def test_healthy_card_ranks_last():
    card = _card(status="At Pickup")
    assert card_priority_rank(card, today=TODAY) == 5


def test_exception_outranks_late_appointment():
    card = _card(exceptions=1, need_date="2026-07-01")
    assert card_priority_rank(card, today=TODAY) == 0


def test_sort_booking_cards_orders_by_rank_then_date_then_booking_number():
    cards = [
        _card(booking="Z-LOW", status="At Pickup"),
        _card(booking="A-EXCEPTION", exceptions=1),
        _card(booking="B-LATE", need_date="2026-07-05"),
        _card(booking="C-LATE-EARLIER", need_date="2026-07-01"),
    ]
    ordered = sort_booking_cards(cards, today=TODAY)
    assert [c["booking_number"] for c in ordered] == ["A-EXCEPTION", "C-LATE-EARLIER", "B-LATE", "Z-LOW"]


def test_sort_booking_cards_stable_tiebreak_on_booking_number():
    cards = [
        _card(booking="MAEU-9999", status="At Pickup"),
        _card(booking="MAEU-1111", status="At Pickup"),
    ]
    ordered = sort_booking_cards(cards, today=TODAY)
    assert [c["booking_number"] for c in ordered] == ["MAEU-1111", "MAEU-9999"]


def test_sort_booking_cards_missing_need_date_sorts_after_dated_cards_in_same_rank():
    cards = [
        _card(booking="NO-DATE", status="At Pickup", need_date=""),
        _card(booking="DATED", status="At Pickup", need_date="2026-07-20"),
    ]
    ordered = sort_booking_cards(cards, today=TODAY)
    assert [c["booking_number"] for c in ordered] == ["DATED", "NO-DATE"]
