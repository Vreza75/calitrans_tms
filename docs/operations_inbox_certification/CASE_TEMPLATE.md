# Case field reference

## case.yaml

```yaml
case_id: CASE-001
name: New Import, Single Container, Email Body Only
request_type: New Booking          # matches classify_customer_request()'s output
service_flow: Import               # Import | Export | Local Import | Local Export
language: English                  # English | Spanish
attachment_types: []               # e.g. [pdf] - drives attachments/ contents
existing_load_required: false      # true for update/change cases (008, 009, ...)
expected_action: Create New Order  # Create New Order | Update Existing Order | Human Review Required
critical_fields:                   # keys from expected.json that MUST match for acceptance
  - intent
  - service_flow
  - customer
  - booking_number
  - containers
noncritical_fields:                # tracked for all_field_accuracy but don't block acceptance
  - pickup
  - delivery
  - dates
  - references

# Only when existing_load_required: true - seeds one row in `loads` before
# processing so the case can exercise matching/update behavior. Column names
# must match the loads table.
seed_load:
  booking_number: GCR-IMP-260801
  container_number: MSCU1234567
  status: Booking Verified
  delivery_need_date: "2026-08-04"
```

## expected.json

The fixed schema (`harness.EXPECTED_SCHEMA_FIELDS`). Every field the case
cares about should be populated with the value a correct, fully-implemented
pipeline should produce - written before any code changes, from the
business requirements in the case's own spec, not from the current code's
output.

```json
{
  "intent": "",
  "service_flow": "",
  "queue": "",
  "decision": "",
  "existing_load_match": null,
  "booking_number": "",
  "order_numbers": [],
  "container_count": 0,
  "containers": [],
  "customer": "",
  "pickup": {},
  "delivery": {},
  "dates": {},
  "references": {},
  "missing_required_fields": [],
  "requires_human_review": false,
  "_critical_fields": []
}
```

`_critical_fields` is harness-specific (not part of the original spec list)
- it lets `harness.compare()` compute `critical_field_accuracy` without
duplicating `case.yaml`'s `critical_fields` list; keep the two in sync.

## email.txt / email.eml

Plain RFC822 headers + blank line + body, parsed with Python's stdlib
`email` module (works for both extensions):

```
Subject: ...
From: Name <email>
Message-ID: <case-NNN@fixtures.calitrans.test>
Date: Mon, 1 Jun 2026 09:00:00 +0000

Body text...
```

Always set an explicit `Message-ID` - it becomes the case's dedupe key
(`services.operations_inbox_service._email_sync_unique_message_id`), which
must be stable across reruns for the duplicate-protection check to mean
anything.

## attachments/

Any file placed here (except `.gitkeep`) is attached to the fixture message
and goes through the real attachment-saving/parsing path
(`_save_operations_email_attachments` / `merge_saved_attachment_fields`).
`.pdf` files are tagged `application/pdf`; everything else is
`application/octet-stream`.
