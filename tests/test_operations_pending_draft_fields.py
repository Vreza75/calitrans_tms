"""_ops_pending_draft_fields_from_parsed's service-flow cleanup used a naive
substring check ("import" in lowered) that fires on "local import" too,
silently collapsing it to plain "Import" - destroying the Local Import /
Local Export distinction every part of the Operations Inbox depends on.
"""
from pages_app.operations_inbox import _ops_pending_draft_fields_from_parsed


def test_local_import_service_flow_is_not_collapsed_to_import():
    fields = _ops_pending_draft_fields_from_parsed({"TYPE": "Local Import"})
    assert fields["service_flow"] == "Local Import"


def test_local_export_service_flow_is_not_collapsed_to_export():
    fields = _ops_pending_draft_fields_from_parsed({"TYPE": "Local Export"})
    assert fields["service_flow"] == "Local Export"


def test_plain_import_service_flow_still_normalizes_to_import():
    fields = _ops_pending_draft_fields_from_parsed({"TYPE": "OTR IMPORT"})
    assert fields["service_flow"] == "Import"


def test_plain_export_service_flow_still_normalizes_to_export():
    fields = _ops_pending_draft_fields_from_parsed({"TYPE": "OTR Export"})
    assert fields["service_flow"] == "Export"
