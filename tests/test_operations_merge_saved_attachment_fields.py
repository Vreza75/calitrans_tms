"""merge_saved_attachment_fields only ever filled blank fields, so clicking
"Parse / Reparse" after fixing a parser bug never actually corrected an
already-populated-but-wrong value (e.g. a Delivery Need Date that had
swallowed adjacent label text from an earlier, buggy extraction) - the
dispatcher had no way to get the corrected value without deleting and
re-uploading the attachment. order_intake.parsed_data has no dispatcher-
confirmed tracking of its own (that lives on the separate pending-draft
table), so an explicit Parse/Reparse click is safe to force-overwrite.
"""
from services.operations_attachment_service import merge_saved_attachment_fields


def test_default_behavior_still_only_fills_blank_fields():
    parsed = {"Delivery Need Date": "2026-06-30 stale extra text"}
    attachments = [{"parsed_data": {"Delivery Need Date": "2026-07-01"}}]

    merged = merge_saved_attachment_fields(parsed, attachments)

    assert merged["Delivery Need Date"] == "2026-06-30 stale extra text"


def test_force_true_lets_a_fresh_reparse_correct_an_already_populated_field():
    parsed = {"Delivery Need Date": "6/30/2026 Customer PO: SSHAES058926 / POL147359"}
    attachments = [{"parsed_data": {"Delivery Need Date": "6/30/2026"}}]

    merged = merge_saved_attachment_fields(parsed, attachments, force=True)

    assert merged["Delivery Need Date"] == "6/30/2026"


def test_force_true_still_skips_fields_the_reparse_did_not_find():
    parsed = {"Customer": "Flat World Global Logistics"}
    attachments = [{"parsed_data": {"Customer": ""}}]

    merged = merge_saved_attachment_fields(parsed, attachments, force=True)

    assert merged["Customer"] == "Flat World Global Logistics"


def test_force_true_still_appends_rather_than_overwrites_dispatcher_notes():
    parsed = {"Dispatcher Notes": "Existing note"}
    attachments = [{"parsed_data": {"Dispatcher Notes": "New note from reparse"}}]

    merged = merge_saved_attachment_fields(parsed, attachments, force=True)

    assert "Existing note" in merged["Dispatcher Notes"]
    assert "New note from reparse" in merged["Dispatcher Notes"]
