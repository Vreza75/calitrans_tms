"""merge_saved_attachment_fields only ever filled blank fields, so clicking
"Parse / Reparse" after fixing a parser bug never actually corrected an
already-populated-but-wrong value (e.g. a Delivery Need Date that had
swallowed adjacent label text from an earlier, buggy extraction) - the
dispatcher had no way to get the corrected value without deleting and
re-uploading the attachment. order_intake.parsed_data has no dispatcher-
confirmed tracking of its own (that lives on the separate pending-draft
table), so an explicit Parse/Reparse click is safe to force-overwrite.
"""
import pytest

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


def test_force_true_never_lets_a_document_scan_overwrite_a_valid_sender_contact_name():
    """The sender's own email header is a stronger identity signal than a
    document-text scan, which can mis-capture an unrelated operational line
    (e.g. a steamship line or carrier name) as if it were a person's name in
    a document that has no real signature block. Contact identity fields
    must stay fill-blank-only regardless of `force`."""
    parsed = {"Contact Name": "Dana Phillips", "Contact Email": "dana.phillips@example.com"}
    attachments = [{"parsed_data": {"Contact Name": "Steamship Line: CMA CGM"}}]

    merged = merge_saved_attachment_fields(parsed, attachments, force=True)

    assert merged["Contact Name"] == "Dana Phillips"


def test_force_true_still_fills_a_blank_contact_name_from_the_document():
    parsed = {"Contact Name": ""}
    attachments = [{"parsed_data": {"Contact Name": "Maria Gonzalez"}}]

    merged = merge_saved_attachment_fields(parsed, attachments, force=True)

    assert merged["Contact Name"] == "Maria Gonzalez"


# --- Identity field merge policy, exhaustive across all four fields --------
#
# Policy (see merge_saved_attachment_fields' docstring): blank or invalid
# existing value -> may be filled by a valid document value; non-blank VALID
# existing value -> always protected, regardless of `force`. Verified here
# for Contact Name, Contact Email, Contact Phone, and Contact Company, not
# just Contact Name.

_IDENTITY_FIELD_EXAMPLES = {
    "Contact Name": {"valid_a": "Dana Phillips", "valid_b": "Maria Gonzalez", "invalid": "Steamship Line: CMA CGM"},
    "Contact Email": {"valid_a": "dana.phillips@example.com", "valid_b": "maria.gonzalez@example.com", "invalid": "not-an-email"},
    "Contact Phone": {"valid_a": "713-555-0101", "valid_b": "281-555-0199", "invalid": "call-the-office"},
    "Contact Company": {"valid_a": "Gulf Coast Logistics Inc", "valid_b": "Coastal Freight Group", "invalid": "info@example.com"},
}


@pytest.mark.parametrize("field", sorted(_IDENTITY_FIELD_EXAMPLES))
@pytest.mark.parametrize("force", [True, False])
def test_identity_field_blank_existing_is_filled_by_valid_document(field, force):
    examples = _IDENTITY_FIELD_EXAMPLES[field]
    parsed = {field: ""}
    attachments = [{"parsed_data": {field: examples["valid_a"]}}]

    merged = merge_saved_attachment_fields(parsed, attachments, force=force)

    assert merged[field] == examples["valid_a"]


@pytest.mark.parametrize("field", sorted(_IDENTITY_FIELD_EXAMPLES))
@pytest.mark.parametrize("force", [True, False])
def test_identity_field_whitespace_only_existing_is_filled_by_valid_document(field, force):
    examples = _IDENTITY_FIELD_EXAMPLES[field]
    parsed = {field: "   "}
    attachments = [{"parsed_data": {field: examples["valid_a"]}}]

    merged = merge_saved_attachment_fields(parsed, attachments, force=force)

    assert merged[field] == examples["valid_a"]


@pytest.mark.parametrize("field", sorted(_IDENTITY_FIELD_EXAMPLES))
@pytest.mark.parametrize("force", [True, False])
def test_identity_field_valid_existing_protected_from_different_valid_document(field, force):
    examples = _IDENTITY_FIELD_EXAMPLES[field]
    parsed = {field: examples["valid_a"]}
    attachments = [{"parsed_data": {field: examples["valid_b"]}}]

    merged = merge_saved_attachment_fields(parsed, attachments, force=force)

    assert merged[field] == examples["valid_a"]


@pytest.mark.parametrize("field", sorted(_IDENTITY_FIELD_EXAMPLES))
@pytest.mark.parametrize("force", [True, False])
def test_identity_field_invalid_existing_is_replaced_by_valid_document(field, force):
    examples = _IDENTITY_FIELD_EXAMPLES[field]
    parsed = {field: examples["invalid"]}
    attachments = [{"parsed_data": {field: examples["valid_a"]}}]

    merged = merge_saved_attachment_fields(parsed, attachments, force=force)

    assert merged[field] == examples["valid_a"]
