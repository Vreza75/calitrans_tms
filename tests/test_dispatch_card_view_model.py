import pandas as pd

from services.dispatch_card_view_model import build_booking_card_view_models


def _row(row_id, booking, customer, move_type, status, driver="", need_date="", lfd="", exceptions=""):
    return {
        "_row_id": row_id,
        "Booking Number": booking,
        "Load ID": f"LOAD-{row_id}",
        "Customer": customer,
        "Dispatch Move Type": move_type,
        "Status": status,
        "Driver Name": driver,
        "Delivery Need Date": need_date,
        "LFD": lfd,
        "Exceptions": exceptions,
    }


def test_same_booking_customer_type_status_groups_into_one_card():
    df = pd.DataFrame([
        _row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(3, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(4, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 1
    assert cards[0]["visible_container_count"] == 4
    assert cards[0]["total_container_count"] == 4
    assert sorted(cards[0]["row_ids"]) == [1, 2, 3, 4]


def test_same_booking_split_across_statuses_produces_separate_cards():
    df = pd.DataFrame([
        _row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(3, "MAEU-5560789", "Bogota Textiles LLC", "Import", "En Route to Pickup"),
        _row(4, "MAEU-5560789", "Bogota Textiles LLC", "Import", "At Pickup"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 3
    by_status = {c["canonical_status"]: c for c in cards}
    assert by_status["Ready to Dispatch"]["visible_container_count"] == 2
    assert by_status["Ready to Dispatch"]["total_container_count"] == 4
    assert by_status["En Route to Pickup"]["visible_container_count"] == 1
    assert by_status["En Route to Pickup"]["total_container_count"] == 4
    assert by_status["At Pickup"]["total_container_count"] == 4


def test_same_booking_number_different_customer_does_not_group():
    df = pd.DataFrame([
        _row(1, "MAEU-1111111", "Customer A", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-1111111", "Customer B", "Import", "Ready to Dispatch"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 2


def test_same_booking_number_different_move_type_does_not_group():
    df = pd.DataFrame([
        _row(1, "MAEU-1111111", "Customer A", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-1111111", "Customer A", "Export", "Ready to Dispatch"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 2


def test_missing_booking_numbers_never_group_together():
    df = pd.DataFrame([
        _row(1, "", "Customer A", "Import", "Ready to Dispatch"),
        _row(2, "", "Customer A", "Import", "Ready to Dispatch"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 2
    assert all(card["total_container_count"] == 1 for card in cards)


def test_missing_booking_number_cards_have_unique_workspace_urls():
    df = pd.DataFrame([
        _row(1, "", "Customer A", "Import", "Ready to Dispatch"),
        _row(2, "", "Customer A", "Import", "Ready to Dispatch"),
    ])
    cards = build_booking_card_view_models(df, df)
    urls = [card["workspace_url"] for card in cards]
    assert len(set(urls)) == 2
    assert all(url.startswith("?load_id=") for url in urls)


def test_booking_number_normalization_trims_and_uppercases_for_grouping_only():
    df = pd.DataFrame([
        _row(1, " maeu-5560789 ", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
    ])
    cards = build_booking_card_view_models(df, df)
    assert len(cards) == 1
    # display value preserves the original casing/whitespace of the first row encountered
    assert cards[0]["booking_number"].strip().upper() == "MAEU-5560789"


def test_totals_reflect_unfiltered_dataset_not_the_scoped_view():
    full_df = pd.DataFrame([
        _row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch"),
        _row(3, "MAEU-5560789", "Bogota Textiles LLC", "Import", "En Route to Pickup"),
        _row(4, "MAEU-5560789", "Bogota Textiles LLC", "Import", "At Pickup"),
    ])
    scoped_df = full_df[full_df["_row_id"].isin([1, 2])]  # simulate an active filter narrowing the view
    cards = build_booking_card_view_models(scoped_df, full_df)
    assert len(cards) == 1
    assert cards[0]["visible_container_count"] == 2
    assert cards[0]["total_container_count"] == 4


def test_unassigned_and_driver_counts():
    df = pd.DataFrame([
        _row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", driver=""),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", driver="Alex"),
        _row(3, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", driver="Sam"),
        _row(4, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", driver=""),
    ])
    cards = build_booking_card_view_models(df, df)
    assert cards[0]["unassigned_count"] == 2
    assert cards[0]["driver_count"] == 2


def test_exception_count_sums_across_group():
    df = pd.DataFrame([
        _row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", exceptions="Late appointment"),
        _row(2, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch", exceptions=""),
    ])
    cards = build_booking_card_view_models(df, df)
    assert cards[0]["exception_count"] == 1


def test_workspace_url_contains_the_booking_number():
    df = pd.DataFrame([_row(1, "MAEU-5560789", "Bogota Textiles LLC", "Import", "Ready to Dispatch")])
    cards = build_booking_card_view_models(df, df)
    assert "MAEU-5560789" in cards[0]["workspace_url"]
    assert cards[0]["workspace_url"].startswith("?booking=")


def test_empty_dataframe_returns_no_cards():
    df = pd.DataFrame(columns=["_row_id", "Booking Number", "Load ID", "Customer", "Dispatch Move Type", "Status", "Driver Name", "Delivery Need Date", "LFD", "Exceptions"])
    assert build_booking_card_view_models(df, df) == []
