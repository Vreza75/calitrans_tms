from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_agents.operations_email_intake_agent import extract_quick_references
from pages_app.operations_inbox import _ops_pending_draft_fields_from_parsed
from services.email_parser import parse_email_text
from services import operations_attachment_service
from services.operations_attachment_service import (
    merge_operations_order_fields,
    parse_operations_attachment_bytes,
)
from services.operations_field_service import (
    extract_operational_fields,
    reconcile_parsed_sources,
)


EXPORT_SUBJECT = "[TMS-TEST]- New booking"
EXPORT_BODY = """Calitrans Team,

Attached is our export dispatch order for booking LSP-EXP-080426. Please
spot the container at our Pasadena warehouse on August 4 and return the
loaded container to Barbours Cut no later than 11:00 AM on August 5.

The port PIN, terminal cutoff, seal, and gross weight are on the attached
order. Please send the driver name and ETA once assigned.

Regards,
Marcus Hill
Export Shipping Supervisor
Lone Star Polymer LLC
(281) 555-0194"""

FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "operations_parsing"
    / "mandatory_export.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_export_email_exact_business_values():
    parsed = parse_email_text(EXPORT_SUBJECT, EXPORT_BODY)

    assert parsed["Booking Number"] == "LSP-EXP-080426"
    assert parsed["Booking Number"] != "CALITRANS"
    assert parsed["Reference Number"] != "LYMER"
    assert parsed["Customer"] == "Lone Star Polymer LLC"
    assert parsed["Contact Name"] == "Marcus Hill"
    assert parsed["Contact Title"] == "Export Shipping Supervisor"
    assert parsed["Contact Company"] == "Lone Star Polymer LLC"
    assert parsed["Contact Phone"] == "(281) 555-0194"
    assert parsed["TYPE"] == "Export"
    assert parsed["Warehouse"]
    assert not parsed["Warehouse"].lower().startswith("on august")
    assert "Pasadena" in parsed["Warehouse"]
    assert "Barbours Cut" in parsed["Port"]


def test_quick_references_use_shared_validated_extraction():
    references = extract_quick_references(EXPORT_SUBJECT, EXPORT_BODY)

    assert references["booking_number"] == "LSP-EXP-080426"
    assert references["booking_number"] != "CALITRANS"
    assert references["reference_number"] == ""
    assert references["reference_number"] != "LYMER"


def test_invalid_email_value_is_corrected_without_true_conflict():
    email_fields = {
        "Booking Number": "CALITRANS",
        "Reference Number": "LYMER",
        "Contact Phone": "(281) 555-0194",
    }
    document_fields = {
        "Booking Number": "LSP-EXP-080426",
        "Reference Number": "SO 784221",
        "Container Number": "CMAU2468101",
        "Size": "40HC",
        "Customer": "Lone Star Polymer LLC",
        "Port": "Barbours Cut Container Terminal",
        "Port PIN": "771204",
        "Warehouse": "Lone Star Polymer Warehouse",
        "Contact Phone": "",
    }

    final_values, rows, conflicts = merge_operations_order_fields(
        email_fields,
        document_fields,
    )

    assert conflicts == []
    assert final_values["Booking Number"] == "LSP-EXP-080426"
    assert final_values["Reference Number"] == "SO 784221"
    assert final_values["Container Number"] == "CMAU2468101"
    assert final_values["Size"] == "40HC"
    assert final_values["Customer"] == "Lone Star Polymer LLC"
    assert final_values["Contact Phone"] == "(281) 555-0194"
    booking_row = next(row for row in rows if row["Field"] == "Booking Number")
    assert booking_row["Status"] == "Document corrected invalid email value"


def test_reconciled_values_project_to_order_draft():
    reconciled, _, conflicts = merge_operations_order_fields(
        {
            "TYPE": "Export",
            "Customer": "Export Shipping Supervisor",
            "Booking Number": "CALITRANS",
            "Contact Phone": "(281) 555-0194",
        },
        {
            "TYPE": "Export",
            "Customer": "Lone Star Polymer LLC",
            "Booking Number": "LSP-EXP-080426",
            "Reference Number": "SO 784221",
            "Container Number": "CMAU2468101",
            "Size": "40HC",
            "Port": "Barbours Cut Container Terminal",
            "Warehouse": "Lone Star Polymer Warehouse",
        },
    )
    draft = _ops_pending_draft_fields_from_parsed(reconciled)

    assert conflicts == []
    assert draft["booking_number"] == "LSP-EXP-080426"
    assert draft["customer"] == "Lone Star Polymer LLC"
    assert draft["container_number"] == "CMAU2468101"
    assert draft["container_size"] == "40HC"
    assert draft["service_flow"] == "Export"


def test_mandatory_export_attachment_exact_values():
    fixture = _fixture()

    _, parsed = parse_operations_attachment_bytes(
        fixture["attachment_text"].encode("utf-8"),
        "lone-star-export-order.txt",
        "text/plain",
    )

    for field, expected in fixture["expected_document"].items():
        assert parsed[field] == expected
    assert parsed["Booking Number"] != "CALITRANS"
    assert parsed["Reference Number"] != "LYMER"


def test_mandatory_export_reconciliation_exact_values():
    fixture = _fixture()
    email_parsed = parse_email_text(
        fixture["subject"],
        fixture["body"],
        fixture["sender"],
    )
    _, document_parsed = parse_operations_attachment_bytes(
        fixture["attachment_text"].encode("utf-8"),
        "lone-star-export-order.txt",
        "text/plain",
    )

    final, _, conflicts = reconcile_parsed_sources(email_parsed, document_parsed)

    assert conflicts == fixture["expected_conflicts"]
    for field, expected in fixture["expected_final"].items():
        assert final[field] == expected
    assert final["_needs_review"] is fixture["expected_needs_review"]
    assert final["Contact Phone"] == "(281) 555-0194"


def test_persisted_reconciled_data_reloads_into_same_order_draft(monkeypatch):
    fixture = _fixture()
    email_parsed = parse_email_text(
        fixture["subject"],
        fixture["body"],
        fixture["sender"],
    )
    _, document_parsed = parse_operations_attachment_bytes(
        fixture["attachment_text"].encode("utf-8"),
        "lone-star-export-order.txt",
        "text/plain",
    )
    final, _, conflicts = reconcile_parsed_sources(email_parsed, document_parsed)
    writes = []
    monkeypatch.setattr(
        operations_attachment_service,
        "execute",
        lambda sql, params: writes.append((sql, params)),
    )

    operations_attachment_service.store_operations_parsed_data(42, final)

    assert conflicts == []
    assert len(writes) == 1
    persisted = json.loads(writes[0][1]["parsed_data"])
    reloaded_work_item = {"id": 42, "parsed_data": persisted}
    draft = _ops_pending_draft_fields_from_parsed(reloaded_work_item["parsed_data"])
    assert persisted["Booking Number"] == "LSP-EXP-080426"
    assert persisted["Container Number"] == "CMAU2468101"
    assert persisted["Size"] == "40HC"
    assert persisted["_needs_review"] is False
    assert writes[0][1]["needs_review"] is False
    assert draft["booking_number"] == "LSP-EXP-080426"
    assert draft["container_number"] == "CMAU2468101"
    assert draft["container_size"] == "40HC"
    assert draft["customer"] == "Lone Star Polymer LLC"


def test_first_invalid_booking_candidate_is_retained_but_not_selected():
    result = extract_operational_fields(
        newest_message=(
            "Booking: CALITRANS\n"
            "Booking: LSP-EXP-080426\n"
            "Customer: Lone Star Polymer LLC\n"
        )
    )

    assert result["fields"]["Booking Number"] == "LSP-EXP-080426"
    invalid = [
        candidate
        for candidate in result["candidates"]
        if candidate["field"] == "Booking Number"
        and candidate["value"] == "CALITRANS"
    ]
    assert len(invalid) == 1
    assert invalid[0]["valid"] is False
    assert invalid[0]["rejection_reason"]


def test_two_valid_different_bookings_require_review():
    parsed = parse_email_text(
        "Export booking",
        "Booking: LSP-EXP-080426\nBooking: LSP-EXP-080427\nCustomer: Lone Star Polymer LLC\nExport",
    )

    assert parsed["Booking Number"] in {"LSP-EXP-080426", "LSP-EXP-080427"}
    assert parsed["_candidate_conflicts"] == ["Booking Number"]
    assert parsed["_needs_review"] is True


def test_quoted_obsolete_booking_is_diagnostic_only():
    parsed = parse_email_text(
        "Re: booking update",
        (
            "Please use booking LSP-EXP-080426.\n\n"
            "From: Old Sender <old@example.com>\n"
            "Sent: Monday, July 20, 2026\n"
            "To: Dispatch <dispatch@calitranscorp.com>\n"
            "Subject: Old booking\n"
            "Booking: OLD-EXP-070126"
        ),
    )

    assert parsed["Booking Number"] == "LSP-EXP-080426"
    quoted = [
        candidate
        for candidate in parsed["_field_candidates"]
        if candidate["source"] == "quoted_history"
        and candidate["field"] == "Booking Number"
    ]
    assert any(candidate["value"] == "OLD-EXP-070126" for candidate in quoted)


def test_multiple_containers_remain_grouped_under_one_booking():
    parsed = parse_email_text(
        "Export booking",
        (
            "Booking: LSP-EXP-080426\n"
            "Containers: CMAU2468101, MSCU1234567, TGHU7654321\n"
            "Customer: Lone Star Polymer LLC\n"
            "Export to Barbours Cut Container Terminal"
        ),
    )

    assert parsed["Booking Number"] == "LSP-EXP-080426"
    assert parsed["Container Number"] == "CMAU2468101"
    assert parsed["Container Numbers"] == [
        "CMAU2468101",
        "MSCU1234567",
        "TGHU7654321",
    ]


def test_spanish_and_mixed_language_fields_use_same_validation():
    parsed = parse_email_text(
        "Nueva reserva de exportación",
        (
            "Cliente: Lone Star Polymer LLC\n"
            "Reserva: LSP-EXP-080426\n"
            "Contenedor: CMAU2468101\n"
            "Puerto: Barbours Cut Container Terminal\n"
            "Warehouse: Pasadena warehouse\n"
            "Favor confirmar la entrega."
        ),
    )

    assert parsed["TYPE"] == "Export"
    assert parsed["Customer"] == "Lone Star Polymer LLC"
    assert parsed["Booking Number"] == "LSP-EXP-080426"
    assert parsed["Container Number"] == "CMAU2468101"
    assert parsed["Port"] == "Barbours Cut Container Terminal"


@pytest.mark.parametrize(
    ("case_id", "expected"),
    [
        (
            "CASE-001",
            {
                "TYPE": "Import",
                "Customer": "Gulf Coast Retail Distribution",
                "Booking Number": "GCR-IMP-260801",
                "Container Number": "MSCU1234567",
                "Size": "40HC",
            },
        ),
        (
            "CASE-002",
            {
                "TYPE": "Export",
                "Customer": "Lone Star Foods",
                "Booking Number": "LSF-EXP-260802",
                "Container Number": "TGHU7654321",
                "Size": "40HC",
            },
        ),
        (
            "CASE-003",
            {
                "TYPE": "Local Import",
                "Customer": "Houston Home Supply",
                "Booking Number": "LI-260803",
                "Container Number": "HJCU2468101",
                "Size": "40FT",
            },
        ),
        (
            "CASE-004",
            {
                "TYPE": "Local Export",
                "Customer": "Texas Industrial Packaging",
                "Booking Number": "LE-260804",
                "Container Number": "OOLU1357913",
                "Size": "20FT",
            },
        ),
    ],
)
def test_existing_service_flow_golden_email_values(case_id, expected):
    from tests.integration.operations_inbox.harness import load_fixture

    fixture = load_fixture(case_id)
    parsed = parse_email_text(
        fixture.message["subject"],
        fixture.message["body"],
        fixture.message["from"],
    )

    for field, expected_value in expected.items():
        assert parsed[field] == expected_value
    assert parsed["Reference Number"] != "LYMER"
    if expected["TYPE"].startswith("Local "):
        assert "Port PIN" not in parsed or parsed["Port PIN"] == ""
        assert "Port PIN" not in parsed["_review_reasons"]["missing_required_fields"]


def test_existing_case_005_real_pdf_exact_attachment_values():
    pdf_path = (
        Path(__file__).parent
        / "fixtures"
        / "operations_inbox"
        / "CASE-005"
        / "attachments"
        / "coastal_appliance_booking.pdf"
    )

    _, parsed = operations_attachment_service.parse_operations_pdf_bytes(
        pdf_path.read_bytes(),
        pdf_path.name,
    )

    assert parsed["TYPE"] == "Import"
    assert parsed["Customer"] == "Coastal Appliance Group"
    assert parsed["Booking Number"] == "CAG-IMP-260805"
    assert parsed["Container Number"] == "CMAU1122334"
    assert parsed["Size"] == "40HC"
    assert parsed["Port"] == "Bayport Container Terminal"
    assert parsed["Warehouse"] == "Coastal Appliance Receiving"
    assert parsed["Address"] == "6300 East Sam Houston Parkway North, Houston, TX 77049"
    assert parsed["Delivery Need Date"] == "August 8, 2026"
    assert parsed["LFD"] == "August 7, 2026"


def test_attachment_parse_success_does_not_hide_persistence_failure(monkeypatch):
    fixture = _fixture()
    _, document_parsed = parse_operations_attachment_bytes(
        fixture["attachment_text"].encode("utf-8"),
        "lone-star-export-order.txt",
        "text/plain",
    )
    monkeypatch.setattr(
        operations_attachment_service,
        "execute",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    assert document_parsed["Booking Number"] == "LSP-EXP-080426"
    with pytest.raises(RuntimeError, match="database unavailable"):
        operations_attachment_service.store_operations_parsed_data(
            42,
            document_parsed,
        )


def test_signature_keeps_name_title_company_email_and_phone_separate():
    parsed = parse_email_text(
        "Export booking LSP-EXP-080426",
        (
            "Please process booking LSP-EXP-080426 for export to Barbours Cut.\n\n"
            "Regards,\n"
            "Marcus Hill\n"
            "Export Shipping Supervisor\n"
            "Lone Star Polymer LLC\n"
            "marcus.hill@lonestarpolymer.example\n"
            "(281) 555-0194"
        ),
    )

    assert parsed["Customer"] == "Lone Star Polymer LLC"
    assert parsed["Contact Name"] == "Marcus Hill"
    assert parsed["Contact Title"] == "Export Shipping Supervisor"
    assert parsed["Contact Company"] == "Lone Star Polymer LLC"
    assert parsed["Contact Email"] == "marcus.hill@lonestarpolymer.example"
    assert parsed["Contact Phone"] == "(281) 555-0194"


def test_forwarded_internal_calitrans_signature_cannot_override_newest_message():
    parsed = parse_email_text(
        "Fwd: corrected export booking",
        (
            "Use booking LSP-EXP-080426 for Lone Star Polymer LLC.\n\n"
            "-----Original Message-----\n"
            "From: CaliTrans Dispatch <dispatch@calitranscorp.com>\n"
            "Sent: Friday, July 24, 2026\n"
            "To: Marcus Hill <marcus.hill@lonestarpolymer.example>\n"
            "Subject: Old export booking\n"
            "Booking: OLD-EXP-070126\n\n"
            "Regards,\n"
            "CaliTrans Dispatch\n"
            "Operations Coordinator"
        ),
        "Marcus Hill <marcus.hill@lonestarpolymer.example>",
    )

    assert parsed["Booking Number"] == "LSP-EXP-080426"
    assert parsed["Booking Number"] != "OLD-EXP-070126"
    assert parsed["Contact Name"] == "Marcus Hill"
    assert parsed["Contact Email"] == "marcus.hill@lonestarpolymer.example"
    assert parsed["Customer"] != "CaliTrans Dispatch"


def test_partial_document_parse_retains_only_supported_values(monkeypatch):
    partial_result = {
        "parser_used": "rule_parser",
        "confidence": 0.50,
        "needs_review": True,
        "parsed_fields": {
            "Booking Number": "LSP-EXP-080426",
            "Customer": "Lone Star Polymer LLC",
        },
        "rule_parser_output": {},
        "document_parser_agent": {},
        "warnings": ["Partial document text."],
    }
    monkeypatch.setattr(
        operations_attachment_service,
        "parse_document_hybrid",
        lambda **_kwargs: partial_result,
    )

    _, parsed = parse_operations_attachment_bytes(
        b"Customer: Lone Star Polymer LLC\nBooking Number: LSP-EXP-080426",
        "partial-order.txt",
        "text/plain",
    )

    assert parsed["Customer"] == "Lone Star Polymer LLC"
    assert parsed["Booking Number"] == "LSP-EXP-080426"
    assert parsed.get("Container Number", "") == ""
    assert parsed.get("Port PIN", "") == ""
    assert parsed["_hybrid_document_parser"]["needs_review"] is True
