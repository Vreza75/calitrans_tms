"""The container-qty number_input widget always needs *some* numeric value
to display, so it fell back to `container_qty or 1` - and the save handler
then wrote that displayed 1 straight back to the draft as a real confirmed
quantity, even when the dispatcher never touched the field and the PDF
never stated a quantity. _resolve_confirmed_container_qty requires an
explicit confirmation before a number_input value is ever persisted.
"""
from pages_app.operations_inbox import _resolve_confirmed_container_qty


def test_unconfirmed_edit_does_not_persist_the_widget_default():
    assert _resolve_confirmed_container_qty(existing_qty=None, edited_qty=1, confirmed=False) is None


def test_confirmed_edit_persists_the_entered_value():
    assert _resolve_confirmed_container_qty(existing_qty=None, edited_qty=4, confirmed=True) == 4


def test_already_confirmed_quantity_stays_confirmed_on_resave():
    assert _resolve_confirmed_container_qty(existing_qty=1, edited_qty=1, confirmed=True) == 1


def test_unchecking_confirmation_reverts_to_needs_review():
    assert _resolve_confirmed_container_qty(existing_qty=1, edited_qty=1, confirmed=False) is None
