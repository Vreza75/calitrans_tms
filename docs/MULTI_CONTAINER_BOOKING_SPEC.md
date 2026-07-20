# CaliTrans Multi-Container Booking Specification

## 1. Purpose

CaliTrans customers may send one booking that requires multiple container moves.

The TMS must treat this as:
Put the exact **business rules, data model, workflow, validation, and test expectations for bookings containing multiple containers** in:

```text
C:\GitHub\calitrans_tms_postgres_upgrade_clean\docs\MULTI_CONTAINER_BOOKING_SPEC.md
```

Paste this complete file:

````markdown
# CaliTrans Multi-Container Booking Specification

## 1. Purpose

CaliTrans customers may send one booking that requires multiple container moves.

The TMS must treat this as:

```text
One parent booking
→ multiple dispatchable child loads
````

It must not create multiple unrelated booking records.

This specification defines how multi-container bookings should be:

* Detected
* Parsed
* Reviewed
* Stored
* Updated
* Converted into child loads
* Dispatched
* Tracked
* Billed
* Protected from duplication

---

## 2. Primary Regression Example

Use the following booking as the primary test fixture:

```text
Customer: Continental Industries Group
Booking Number: RICGX1235800
Reference Number: SO217089a/C25749C
Service Flow: Export
Containers Required: 4
Container Size: 40HC
Known Physical Container Numbers: None initially
Commodity: Resin Non Haz
Cargo Pickup Warehouse: PBP Packaging
Empty Pickup Location: ConGlobal-La Porte
Full Return Terminal: Bayport Terminal
Steamship Line: ONE
Vessel: CONTI CORTESIA 013W
Port of Loading: Houston
Port of Discharge: Kaohsiung
Documentation Cutoff: 2026-07-22 12:00
VGM Cutoff: 2026-07-24 12:00
Cargo Cutoff: 2026-07-24 16:00
Sailing Date: 2026-07-27
ETA Date: 2026-08-31
```

Expected classification:

```text
Queue: New Orders
Request Type: New Booking
Department: Dispatch
Service Flow: Export
```

---

## 3. Core Business Rule

A multi-container booking consists of:

### One parent booking

The parent booking stores shared information such as:

* Customer
* Booking number
* Reference number
* Service flow
* Container quantity
* Container size
* Commodity
* Warehouse
* Empty pickup
* Full return terminal
* Carrier
* Vessel
* Port information
* Cutoff dates
* Customer instructions
* Communication history

### Multiple child loads

Each required container move becomes one dispatchable child load.

For a booking requiring four containers:

```text
Child Load 1 of 4
Child Load 2 of 4
Child Load 3 of 4
Child Load 4 of 4
```

Each child load may later have its own:

* Physical container number
* Driver
* Truck
* Chassis
* Pickup appointment
* Delivery appointment
* Status
* POD
* Empty return
* Billing status
* Notes

---

## 4. Booking Number Versus Container Number

The booking number is not a physical container number.

For the regression example:

```text
RICGX1235800
```

is the booking or bill-of-lading reference.

It must not be saved as:

```text
Container Number = RICGX1235800
```

Physical container numbers usually follow a format similar to:

```text
ABCD1234567
```

When physical container numbers are not known, the child loads should use:

```text
container_number = NULL
```

or an empty value displayed as:

```text
Pending Assignment
```

---

## 5. Required Parent Booking Fields

The parent booking or pending order draft should support:

```text
customer
booking_number
reference_number
service_flow
container_qty
container_size
commodity
cargo_pickup_warehouse
delivery_address
empty_pickup_location
full_return_terminal
port_of_loading
port_of_discharge
place_of_delivery
steamship_line
vessel_name
opens_for_receiving
document_cutoff
port_cutoff
vgm_cutoff
cargo_cutoff
sailing_date
eta_date
container_free_time_days
chassis_free_time
dispatcher_notes
conversation_key
draft_status
request_stage
field_provenance
parse_profile
parse_version
parse_errors
created_load_id
containers_created
```

The parent booking should not require a physical container number.

---

## 6. Required Child Load Fields

Each child load should support:

```text
parent_booking_key
booking_number
reference_number
container_sequence
container_total
is_placeholder_container
container_number
container_size
customer
service_flow
commodity
warehouse
delivery_address
empty_pickup_location
full_return_terminal
port_of_loading
port_of_discharge
steamship_line
vessel_name
status
driver_name
truck_assigned
chassis
notes
```

Example:

```text
parent_booking_key = RICGX1235800
container_sequence = 1
container_total = 4
is_placeholder_container = true
container_number = NULL
```

---

## 7. Required Database Relationship

The parent booking should be linked to child loads using:

```text
parent_booking_key
```

Recommended value:

```text
parent_booking_key = booking_number
```

For the regression example:

```text
parent_booking_key = RICGX1235800
```

Recommended child uniqueness:

```text
parent_booking_key + container_sequence
```

This combination must be unique.

Example:

```text
RICGX1235800 + 1
RICGX1235800 + 2
RICGX1235800 + 3
RICGX1235800 + 4
```

The system must prevent:

```text
RICGX1235800 + 1
RICGX1235800 + 1
```

---

## 8. Container Quantity Rules

Container quantity must be a positive integer.

Valid examples:

```text
1
2
4
10
```

Invalid examples:

```text
NULL when the source clearly provides a quantity
0
-1
4 X40 stored as the integer field
Four
PBP PACKAGING
```

The parser may receive formats such as:

```text
4 X40
4 X 40HC
4X40HC
4 containers
NUMBER OF CNTRS: 4
CONTAINER QTY: 4 X40
```

These should normalize to:

```text
container_qty = 4
container_size = 40HC
```

---

## 9. Unknown Quantity Rule

Never silently default an unknown quantity to `1`.

Do not use:

```python
container_qty = value or 1
```

When quantity is missing or invalid:

```text
container_qty = NULL
draft_status = Needs Details
```

The user interface should display:

```text
Containers Required: Needs Review
```

The Create Container Work Orders action must be disabled until quantity is valid.

---

## 10. Container Size Normalization

Normalize common values:

```text
40HQ → 40HC
40 HC → 40HC
40H → 40HC when document context confirms high cube
40 → Needs Review unless document context confirms the equipment type
20GP → 20
20DV → 20
45HQ → 45HC
```

For the regression booking:

```text
4 X40
```

combined with the booking PDF should normalize to:

```text
container_qty = 4
container_size = 40HC
```

The parser should preserve the original text in source evidence.

---

## 11. Parsing Sources

The system may receive booking information from:

* Email subject
* Plain-text email body
* HTML email table
* PDF booking confirmation
* Word document
* Customer reply
* Dispatcher correction
* LLM suggestion

Use this source precedence:

```text
Dispatcher-confirmed value
> specialized booking-document parser
> structured email-table parser
> validated generic parser
> LLM suggestion
```

A weaker source must not overwrite a stronger source.

---

## 12. Field Provenance

Store where each important value came from.

Example:

```json
{
  "Booking Number": {
    "value": "RICGX1235800",
    "source": "pdf_booking_confirmation",
    "parser": "continental_fcl_booking_v1"
  },
  "Container Qty": {
    "value": 4,
    "source": "pdf_booking_confirmation",
    "parser": "continental_fcl_booking_v1"
  },
  "Warehouse": {
    "value": "PBP Packaging",
    "source": "customer_email_table"
  }
}
```

Dispatcher-confirmed values should be marked as:

```text
source = dispatcher_confirmed
```

They must not be overwritten automatically.

---

## 13. Required Specialized Parser

Stable Continental booking confirmations should use a dedicated profile such as:

```text
continental_fcl_booking_v1
```

The parser should extract:

* Customer
* Booking Number
* Reference Number
* Container Quantity
* Container Size
* Commodity
* Warehouse
* Trucker
* Empty Pickup
* Full Return Terminal
* Steamship Line
* Vessel
* Port of Loading
* Port of Discharge
* Place of Delivery
* Opens for Receiving
* Documentation Cutoff
* VGM Cutoff
* Cargo Cutoff
* Sailing Date
* ETA Date
* Container Free Time
* Chassis Free Time
* Invoice Contact

The parser should distinguish operational shipment data from passive billing language.

---

## 14. Classification Rule

A booking confirmation remains a New Booking even when the document contains:

* Bill of lading
* House bill of lading
* Ocean bill of lading
* Rate sheet
* Invoice contact
* Charges will be invoiced
* This document is not an invoice

These terms do not make the email a Billing request.

Billing requires an actionable request such as:

```text
Please send the invoice.
Please correct invoice 123.
What is the payment status?
The detention invoice is incorrect.
```

---

## 15. Pending Draft Workflow

When a multi-container booking arrives:

```text
Email received
→ email body normalized
→ HTML table parsed
→ booking PDF parsed
→ fields merged using source authority
→ one pending order draft created
→ dispatcher reviews latest agreed information
→ missing or polluted values corrected
→ draft marked Ready for Order Creation
→ dispatcher confirms child-load creation
```

The draft should remain tied to the same business conversation.

---

## 16. Latest Agreed Information

Customer replies may update booking information before child loads are created.

Example:

```text
Original:
Cargo cutoff July 24 at 16:00

Customer reply:
Please change the cargo cutoff to July 25 at 14:00
```

The pending draft should show:

```text
Cargo Cutoff = 2026-07-25 14:00
```

Communication history must preserve both messages.

The draft should record:

```text
Previous value
New value
Source message
Timestamp
Dispatcher confirmation status
```

---

## 17. Draft Readiness

A draft should not become ready merely because a booking number was found.

Recommended minimum readiness checks:

* Customer exists
* Booking number exists
* Service flow is valid
* Container quantity is a positive integer
* Container size is valid
* Required pickup location is reviewed
* Required return or destination is reviewed
* No polluted critical fields
* Required booking attachment has been parsed
* Dispatcher has reviewed ambiguous values

Suggested statuses:

```text
Awaiting Document Parse
Needs Details
Needs Dispatcher Review
Ready for Order Creation
Container Work Orders Created
Cancelled
```

---

## 18. Polluted Value Protection

Do not save fields that contain unrelated embedded labels.

Invalid example:

```text
Reference Number =
SO217089a/C25749C CONTAINER QTY: 4 X40 BAG TYPE: 25 KG BAGS
```

Invalid example:

```text
Warehouse =
PBP PACKAGING TRUCKER: CALI TRANS
```

Invalid example:

```text
Service Flow =
25 KG BAGS 0.025 VOLUME
```

If a value contains labels such as:

```text
CONTAINER QTY:
BAG TYPE:
WAREHOUSE:
TRUCKER:
BOOKING:
DOC CUT OFF:
PORT CUTOFF:
```

the system should:

1. Reject the value.
2. Record a parse error.
3. Mark the field for review.
4. Avoid overwriting a valid existing value.

---

## 19. Dispatcher Review Interface

The Active Pending Order Draft should show:

### Booking summary

* Draft status
* Stage
* Booking number
* Reference
* Customer
* Service flow
* Container size
* Known physical container number

### Quantity summary

* Containers required
* Containers created
* Remaining containers

### Route and export details

* Cargo pickup warehouse
* Empty pickup
* Full return
* Port of loading
* Port of discharge
* Vessel
* Carrier
* Documentation cutoff
* VGM cutoff
* Cargo cutoff
* Sailing
* ETA

### Editable latest agreed information

The dispatcher should be able to correct:

* Customer
* Booking
* Reference
* Service flow
* Quantity
* Size
* Commodity
* Locations
* Dates
* Notes

### Action

The dispatcher should see:

```text
Create 4 Container Work Orders
```

only when the draft is ready.

---

## 20. Child Load Creation Rules

When the dispatcher confirms creation:

1. Read the requested container quantity.
2. Query existing child loads.
3. Determine existing sequences.
4. Determine missing sequences.
5. Create only missing child loads.
6. Use one database transaction when possible.
7. Return created, existing, and failed results.
8. Recalculate the child count from the database.
9. Update the parent draft status.

For quantity 4 with no existing children:

```text
Create sequences 1, 2, 3, and 4
```

For quantity 4 with sequences 1 and 2 already present:

```text
Create only sequences 3 and 4
```

---

## 21. Idempotency Requirement

Child-load creation must be idempotent.

Running the same action twice should produce:

### First execution

```text
Created: 4
Existing: 0
Failed: 0
```

### Second execution

```text
Created: 0
Existing: 4
Failed: 0
```

It must not produce eight child loads.

---

## 22. Transaction Requirements

Multi-load creation should use a database transaction.

If one child fails:

* Avoid leaving an unknown partial state.
* Roll back all children when practical.
* Otherwise record exactly which sequences succeeded.
* Allow a safe retry that creates only missing sequences.

The dispatcher should receive a clear result.

---

## 23. Child Load Initial Status

Recommended initial status:

```text
New
```

or:

```text
Pending Container Assignment
```

Each child should begin with:

```text
is_placeholder_container = true
container_number = NULL
```

When a physical container number is assigned:

```text
is_placeholder_container = false
container_number = ABCD1234567
```

---

## 24. Physical Container Assignment

Actual container numbers may arrive from:

* Customer reply
* Terminal message
* Driver
* Dispatcher entry
* Booking update
* Port integration

The system should allow the dispatcher to assign container numbers to existing child sequences.

Example:

```text
Sequence 1 → TGHU7654321
Sequence 2 → OOLU1234567
Sequence 3 → Pending
Sequence 4 → Pending
```

Do not create a new child load simply because a container number is later supplied when an unassigned placeholder already exists.

---

## 25. Dispatch Behavior

Each child load should be independently dispatchable.

Each child may have:

* Different driver
* Different truck
* Different chassis
* Different appointment
* Different pickup time
* Different delivery time
* Different status
* Different POD
* Different empty return

Shared booking fields should remain linked to the parent booking.

---

## 26. Status Rollup

The parent booking may display a rollup such as:

```text
0 of 4 Assigned
2 of 4 Picked Up
1 of 4 Delivered
1 of 4 Completed
```

Recommended parent statuses:

```text
Draft
Ready for Creation
Created
Partially Assigned
In Progress
Partially Delivered
Completed
Cancelled
```

Do not mark the parent booking completed until all required child loads are complete or cancelled.

---

## 27. Billing Behavior

Each child container may be:

* Billed individually
* Included in one consolidated booking invoice
* Grouped according to customer requirements

The TMS should not automatically use ocean freight rate-sheet values as CaliTrans trucking rates.

Source document financial values should remain separate from:

```text
CaliTrans customer rate
Driver pay
Accessorial charges
Final invoice amount
```

Billing behavior should remain configurable.

---

## 28. Communication History

All messages related to the parent booking should remain in one business conversation.

History should include:

* Original booking email
* Booking PDF
* Dispatcher reply
* Customer corrections
* Container assignments
* Appointment changes
* Gate changes
* Status requests
* Documentation messages
* Billing messages

Child-load-specific communications may link to both:

```text
parent booking
child load
```

---

## 29. Cancellation and Quantity Changes

### Quantity increase

If the customer changes:

```text
4 containers → 6 containers
```

the system should:

* Update `container_qty` to 6.
* Preserve existing child loads.
* Offer creation of sequences 5 and 6.

### Quantity decrease

If the customer changes:

```text
4 containers → 3 containers
```

the system should not automatically delete an existing load.

It should:

* Identify the extra child.
* Show its current operational status.
* Require dispatcher confirmation.
* Cancel or remove only when safe.

### Booking cancellation

Cancellation should:

* Preserve history.
* Mark the parent cancelled.
* Mark or cancel unstarted child loads.
* Require review for loads already dispatched or completed.

---

## 30. Database Requirements

Recommended parent draft columns:

```text
container_qty integer nullable
containers_created integer default 0
load_group_key text
field_provenance jsonb
parse_errors jsonb
```

Recommended child load columns:

```text
parent_booking_key text
container_sequence integer
container_total integer
is_placeholder_container boolean
```

Recommended unique protection:

```sql
create unique index if not exists
    ux_loads_parent_booking_sequence
on public.loads (
    parent_booking_key,
    container_sequence
)
where parent_booking_key is not null
  and container_sequence is not null;
```

Before adding the index, check for existing duplicates.

---

## 31. API or Service Contract

The multi-container creation service should accept a request similar to:

```json
{
  "draft_id": 123,
  "booking_number": "RICGX1235800",
  "container_qty": 4,
  "container_size": "40HC",
  "confirmed_by": "dispatcher"
}
```

Suggested result:

```json
{
  "success": true,
  "parent_booking_key": "RICGX1235800",
  "requested": 4,
  "created_sequences": [1, 2, 3, 4],
  "existing_sequences": [],
  "failed_sequences": [],
  "total_children": 4
}
```

On repeated execution:

```json
{
  "success": true,
  "parent_booking_key": "RICGX1235800",
  "requested": 4,
  "created_sequences": [],
  "existing_sequences": [1, 2, 3, 4],
  "failed_sequences": [],
  "total_children": 4
}
```

---

## 32. Required Automated Tests

### Parsing tests

For booking `RICGX1235800`:

```text
Booking Number = RICGX1235800
Reference Number = SO217089a/C25749C
Container Qty = 4
Container Size = 40HC
Service Flow = Export
Customer = Continental Industries Group
Warehouse = PBP Packaging
Empty Pickup = ConGlobal-La Porte
Full Return Terminal = Bayport Terminal
Carrier = ONE
Vessel = CONTI CORTESIA 013W
```

### Classification tests

The booking remains New Orders despite:

```text
Bill of lading
Invoice contact
Charges will be invoiced
This document is not an invoice
```

### Draft tests

* One booking creates one draft.
* Replies update the same draft.
* Quantity remains 4.
* Unknown quantity does not become 1.
* Polluted fields are rejected.

### Creation tests

* Quantity 4 creates exactly four child loads.
* Child sequences are 1–4.
* All children use the same parent booking key.
* Physical container numbers remain blank.
* Repeated creation creates no duplicates.
* Existing children are counted from the database.
* Partial creation can resume safely.

### Quantity-change tests

* Increase from 4 to 6 offers sequences 5 and 6.
* Decrease does not silently delete child loads.

---

## 33. Required Manual Acceptance Test

1. Send the RICGX1235800 test email.
2. Attach the booking PDF.
3. Run quick email sync.
4. Open the work item.
5. Confirm it appears in New Orders.
6. Confirm the attachment is parsed.
7. Confirm the pending draft shows:

   * Quantity 4
   * Size 40HC
   * Export
   * Correct booking and reference
8. Confirm no physical container number is invented.
9. Confirm the Create 4 Container Work Orders button is enabled only after review.
10. Create the work orders.
11. Confirm four child loads exist.
12. Run creation again.
13. Confirm zero duplicates are created.
14. Assign one physical container number.
15. Confirm the existing placeholder is updated rather than creating a fifth load.

---

## 34. Non-Goals

This specification does not require:

* Automatic creation without dispatcher confirmation
* Automatic driver assignment
* Automatic billing
* Automatic physical container-number invention
* A full FastAPI migration
* A background worker before the current workflow is stable
* Replacing deterministic parsers with an LLM

---

## 35. Definition of Done

Multi-container booking support is complete when:

* One booking can represent multiple container moves.
* Quantity is parsed correctly.
* Unknown quantity remains unknown.
* Booking number is not treated as a container number.
* One parent booking links to multiple child loads.
* Child creation is idempotent.
* Physical container numbers can be assigned later.
* Quantity changes are handled safely.
* Communication history remains linked.
* Dispatcher confirmation is required.
* Automated regression tests pass.


