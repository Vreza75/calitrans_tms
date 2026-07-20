# CaliTrans Operations Inbox Requirements

## 1. Purpose

The Operations Inbox is the central operational work-management workspace for CaliTrans.

It must do more than display email. It should convert incoming customer and operational messages into organized, traceable work that the dispatcher can review and complete.

The Operations Inbox should support the full flow from:
Put the detailed **functional requirements and expected behavior** for the Operations Inbox in:

```text
C:\GitHub\calitrans_tms_postgres_upgrade_clean\docs\OPERATIONS_INBOX_REQUIREMENTS.md
```

Paste the following complete file:

````markdown
# CaliTrans Operations Inbox Requirements

## 1. Purpose

The Operations Inbox is the central operational work-management workspace for CaliTrans.

It must do more than display email. It should convert incoming customer and operational messages into organized, traceable work that the dispatcher can review and complete.

The Operations Inbox should support the full flow from:

```text
Incoming customer email
→ email and attachment review
→ operational classification
→ booking or load matching
→ dispatcher decision
→ customer reply
→ load creation or load update
→ driver and delivery activity
→ document collection
→ billing readiness
→ completion and archive
````

Human review remains authoritative. AI and automation should assist the dispatcher but should not execute irreversible operational actions without confirmation.

---

## 2. Business Context

CaliTrans is a small drayage and transportation company with approximately:

* 10–20 drivers
* One dispatcher
* One manager
* One accounting representative

The Operations Inbox must remain practical for a small office team.

The system should prioritize:

* Clear work queues
* Fast email review
* Accurate booking extraction
* Reliable customer communication
* Traceable decisions
* Low operating cost
* Simple workflows
* Reduced duplicate data entry

The company handles:

* Import drayage
* Export drayage
* Local import moves
* Local export moves
* Warehouse-to-warehouse transportation
* New customer bookings
* Booking changes
* Terminal and gate changes
* Appointment and PIN requests
* Driver and delivery status updates
* POD and document requests
* Billing and invoice requests
* Port and steamship-line communications

The system should support customer messages in English and Spanish.

---

## 3. Primary Users

### Dispatcher

The dispatcher should be able to:

* Review incoming operational messages
* See the recommended queue and action
* Review the original message and attachments
* Review parsed booking or load information
* Correct classification and extracted fields
* Match a message to an existing load
* Maintain a pending order draft
* Create a new load or multiple child loads
* Update an existing load
* Reply to the customer
* Mark work as waiting on the customer
* Close or archive the work item
* Review the complete communication history

### Manager

The manager should be able to:

* Review queue volume
* Review unresolved operational cases
* Review dispatcher actions
* Review classification accuracy
* Review exceptions and escalations
* Review AI and parser performance
* Review workload and operational risk

### Accounting Representative

The accounting representative should be able to:

* Review true billing requests
* Review POD and document readiness
* Review invoice-related messages
* Review completed-load documentation
* Review billing exceptions
* Avoid receiving ordinary booking confirmations in the Billing queue

---

## 4. Supported Service Flows

Only the following normalized service-flow values are valid:

```text
Import
Export
Local Import
Local Export
```

The system must not save arbitrary extracted text as the service flow.

Examples of invalid service-flow values:

```text
25 KG BAGS
PBP PACKAGING
BAYPORT TERMINAL
CONTAINER QTY
```

When the system cannot determine the service flow confidently, it should show:

```text
Needs Review
```

---

## 5. Supported Work Types

The Operations Inbox should recognize and manage:

* New Booking
* Pending Order Reply
* Booking Update
* Quote Request
* Appointment Request
* PIN Request
* Driver Status Update
* Delivery Status Update
* Terminal Change
* Gate Change
* Port Issue
* Cancellation
* POD Request
* Document Request
* Actual Billing Request
* Invoice Correction
* Payment Question
* No Action / FYI
* Spam / Marketing
* Needs Classification

---

## 6. Operations Queue Structure

The primary queues should be:

```text
New Orders
Quotes
Existing Load Updates
Appointments / PIN
Documents
Billing
Needs Review
Store Only / Archive
```

### Queue precedence

The system should apply this precedence consistently:

1. Spam / marketing / no-action archive
2. Quote request
3. Authoritative booking confirmation / new order
4. Appointment or PIN
5. Actual actionable billing request
6. POD or document request
7. Existing-load update
8. Needs review

The same classification result must be used by:

* Email synchronization
* Fast triage
* Recheck Next Batch
* Queue table
* Dispatcher Decision
* Pending order draft
* Learning feedback

The system must not implement conflicting queue logic independently in several files.

---

## 7. New Booking Versus Billing

A booking confirmation must not be routed to Billing merely because it contains terms such as:

* Bill of lading
* Ocean bill of lading
* House bill of lading
* Invoice contact
* Charges will be invoiced
* Rate sheet
* This document is not an invoice
* Invoice should be sent to

These are passive document terms.

A message should route to Billing only when it contains an actionable billing request, such as:

* Please send the invoice
* Please correct the invoice
* Invoice status
* Missing invoice
* Incorrect invoice
* Billing dispute
* Payment issue
* Detention invoice
* Demurrage invoice
* Accessorial billing request

A clear booking confirmation must take priority over passive billing terminology.

---

## 8. Email Intake Requirements

For every synchronized email, preserve:

* Source mailbox
* Email direction
* Sender
* Recipients
* CC recipients
* Subject
* Received timestamp
* Sent timestamp
* Message-ID
* In-Reply-To
* References
* Provider thread ID
* Raw plain-text body
* Raw HTML body
* Normalized body text
* Attachment metadata
* Attachment filenames
* Attachment types
* Conversation key
* Classification result
* Review status
* Work status

The system must preserve enough metadata to reconstruct a complete communication thread.

---

## 9. HTML Email and Table Handling

The system must not discard structured information contained in HTML email tables.

When an email contains a table, the table should be converted into normalized structured text.

Example:

```text
RELEASE REF: SO217089a/C25749C - RICGX1235800
CONTAINER QTY: 4 X40
BAG TYPE: 25 KG BAGS
VOLUME (MT): 99
TOTAL BAG: 3960
GRADE: HDPE HD5207F
FFW: FLEUR DE LIS
WAREHOUSE: PBP PACKAGING
TRUCKER: CALI TRANS
BOOKING: RICGX1235800
DOC CUT OFF: 7/22
PORT CUTOFF: 7/24
```

The normalized table content must be available to:

* Deterministic parsers
* AI review
* Dispatcher review
* Pending order drafts
* Audit history

---

## 10. Parsing Requirements

The Operations Inbox should use layered parsing.

### Layer 1 — Email normalization

Normalize:

* Plain text
* HTML
* HTML tables
* Forwarded content
* Signatures
* Previous-message quotations

### Layer 2 — Structured email parser

Parse explicit labels found in the email body.

Examples:

* Customer
* Booking
* Reference
* Container Quantity
* Container Size
* Warehouse
* Trucker
* Appointment
* PIN
* Delivery Date
* Port Cutoff
* Document Cutoff

### Layer 3 — Specialized document parser

Stable customer or carrier formats should have dedicated parser profiles.

Example:

```text
continental_fcl_booking_v1
```

### Layer 4 — Generic parser

The generic parser may fill blank fields but must not overwrite validated specialized-parser values.

### Layer 5 — LLM fallback

Use an LLM only when deterministic parsing remains ambiguous.

The LLM result must remain a suggestion until reviewed.

---

## 11. Parsing Source Authority

Use this field-source precedence:

```text
Dispatcher-confirmed value
> specialized document parser
> structured email-table parser
> validated generic parser
> LLM suggestion
```

A weaker source must not overwrite a stronger source.

The system should store field provenance, including:

```text
Field name
Extracted value
Source type
Source document
Parser profile
Parser version
Confidence
Review status
```

Example:

```json
{
  "Booking Number": "pdf_booking_confirmation",
  "Container Qty": "pdf_booking_confirmation",
  "Warehouse": "customer_email_table",
  "Trucker": "customer_email_table"
}
```

---

## 12. Field Validation

The system must reject polluted values that contain unrelated field labels.

Examples of polluted values:

```text
Reference Number:
SO217089a/C25749C CONTAINER QTY 4 X40 BAG TYPE 25 KG BAGS

Warehouse:
PBP PACKAGING TRUCKER: CALI TRANS

Service Flow:
25 KG BAGS 0.025 VOLUME
```

Values containing embedded labels such as the following should require review:

```text
CONTAINER QTY:
BAG TYPE:
TRUCKER:
BOOKING:
DOC CUT OFF:
PORT CUTOFF:
WAREHOUSE:
```

The system should not save invalid parsed data into the pending draft.

---

## 13. Conversation Key Requirements

The system should use one canonical business conversation key.

Preferred identifiers:

1. Booking number
2. Physical container number
3. Customer reference
4. Provider thread ID
5. Normalized subject
6. Fallback intake identifier

All messages relating to the same booking or load should share the same business conversation.

Do not calculate conflicting conversation keys in multiple places.

---

## 14. Communication History

Communication History must:

* Show inbound and outbound messages
* Show messages in chronological order
* Include only the relevant business conversation
* Exclude unrelated mailbox messages
* Show message direction
* Show sender
* Show subject
* Show time
* Show request status
* Show thread status
* Show body preview
* Allow the dispatcher to open the complete message
* Preserve historical traceability

Clicking a history row should display:

* Full sender information
* Full subject
* Full message body
* Parsed fields
* Attachment information
* Message-ID and thread metadata when needed for diagnostics

A booking conversation containing four messages should show four relevant messages, not hundreds of unrelated emails.

---

## 15. Active Work-Item Rules

Only the newest actionable message for a business conversation should appear in the active queue.

Do not show:

* The original message
* Every customer reply
* Every dispatcher reply

as separate active work items for the same conversation.

After a dispatcher sends a reply and marks the work item as waiting on the customer:

* Save the outbound message
* Set conversation status to Waiting Customer
* Close the current active request
* Remove it from the active queue
* Preserve it in communication history

When the customer replies:

* Reopen the same business conversation
* Create a new actionable work item for the newest inbound request
* Preserve the existing pending draft or matched load
* Set thread status to Customer Replied

---

## 16. Dispatcher Decision Requirements

The Dispatcher Decision section should combine:

* Current message
* Communication history
* Parsed fields
* Attachment evidence
* Pending order draft
* Existing-load matches
* Classification confidence
* Recommended action
* Missing information
* AI recommendation when requested

The dispatcher should see the evidence before taking action.

The system should not display a final action without making the supporting message and extracted details accessible.

Possible actions include:

* Review Order Draft
* Create Order
* Create Container Work Orders
* Find / Match Existing Load
* Update Existing Load
* Request Missing Information
* Review Attachment
* Reply to Customer
* Open Case
* Close / No Action
* Archive

---

## 17. Pending Order Draft Requirements

When a new booking has not yet created a load, maintain one persistent pending order draft.

The draft must:

* Remain tied to the business conversation
* Merge information from later customer replies
* Preserve the latest agreed values
* Preserve dispatcher-confirmed corrections
* Preserve source provenance
* Show missing required fields
* Allow manual dispatcher edits
* Remain active until order creation or cancellation
* Support one booking with multiple container moves

Recommended statuses:

```text
Awaiting Document Parse
Needs Details
Needs Dispatcher Review
Ready for Order Creation
Container Work Orders Created
Cancelled
```

The draft should not silently become ready when required fields are invalid or unknown.

---

## 18. Latest Agreed Order Information

The pending draft should display editable final values such as:

* Customer
* Booking Number
* Reference Number
* Service Flow
* Container Quantity
* Container Size
* Known Container Number
* Commodity
* Origin / Port
* Cargo Pickup Warehouse
* Empty Pickup Location
* Full Return Terminal
* Destination
* Delivery Address
* Delivery Need Date
* Documentation Cutoff
* VGM Cutoff
* Cargo Cutoff
* Vessel
* Steamship Line
* Dispatcher Notes

When later messages change a field, the system should update the same pending draft.

Example:

```text
Original request:
Delivery date July 10

Customer reply:
Change delivery date to July 14
```

The pending draft should show:

```text
Delivery Need Date = July 14
```

The history must still preserve both messages.

---

## 19. Multi-Container Booking Requirements

One booking may require multiple container moves.

Example:

```text
Booking: RICGX1235800
Containers Required: 4
Container Size: 40HC
```

Correct structure:

```text
One parent booking or pending draft
Four child load placeholders
```

Child loads should contain:

* Parent booking key
* Container sequence
* Container total
* Placeholder status
* Booking number
* Container size
* Service flow
* Shared route details
* Blank physical container number until known

Example:

```text
Container 1 of 4
Container 2 of 4
Container 3 of 4
Container 4 of 4
```

The system must not treat the booking number as a physical container number.

The system must not default an unknown container quantity to `1`.

Unknown quantity should show:

```text
Needs Review
```

Child-load creation must be idempotent.

Repeating the creation action must not create duplicate child loads.

---

## 20. Existing Load Matching

When an email appears to relate to an existing load, the system should search using:

* Booking number
* Container number
* Reference number
* Customer
* Normalized subject
* Conversation key

Load matching should run on demand when possible to keep the page responsive.

The dispatcher should see:

* Candidate load ID
* Booking
* Container
* Customer
* Status
* Match reason
* Match confidence

The dispatcher must confirm uncertain matches.

---

## 21. Email Action Center

The Primary Email Action Center should keep these controls together:

* Email action
* Reply from
* To
* CC
* Subject
* AI reply draft
* Standard template
* Clear draft
* Editable message body
* Waiting-customer checkbox
* Send or record action button

The send button must remain visible and accessible.

AI reply assistance should be inside the email response section, not separated at the bottom of the page.

The original email does not need to be duplicated in multiple sections when the current message and draft evidence are already visible.

---

## 22. Outbound Email Requirements

When an email is successfully sent:

* Send through the selected CaliTrans mailbox
* Save the outbound communication
* Preserve subject and body
* Preserve recipients
* Link it to the conversation
* Link it to the load when applicable
* Link it to the operational case when applicable
* Update conversation status
* Close the active work item when appropriate
* Prevent duplicate outbound database records
* Display a success message

When sending fails:

* Display the error
* Save the failure status when possible
* Do not mark the work item completed
* Do not claim the message was sent

---

## 23. AI Assistance Requirements

AI may assist with:

* Classification
* Suggested queue
* Suggested action
* Load-match recommendation
* Missing-field identification
* Reply drafting
* English/Spanish response generation
* Learning from dispatcher corrections

AI must not:

* Send email without confirmation
* Create a load without confirmation
* Update a load without confirmation
* Change financial data without confirmation
* Replace deterministic parsing for stable customer formats

The dispatcher should be able to accept, edit, or reject AI suggestions.

---

## 24. Feedback and Learning

The system should save dispatcher feedback such as:

* Classification accepted
* Classification corrected
* Reply accepted
* Reply edited
* Load match accepted
* Load match rejected
* Parser field corrected
* Final action selected
* Optional feedback notes

Learning feedback should improve future suggestions but should not automatically override validated rules.

---

## 25. Attachment Requirements

The system should support:

* PDF
* Word documents
* Text files
* Common image attachments when supported

Attachment processing should:

1. Save attachment metadata.
2. Preserve the original filename.
3. Determine document type.
4. Run the correct parser profile.
5. Save extracted fields.
6. Save parser errors.
7. Merge fields using source authority.
8. Allow dispatcher review.

Quick sync should not deeply parse every attachment across every mailbox message.

Deep parsing should happen:

* For likely new bookings
* On selected-message demand
* In a future background worker

---

## 26. Email Sync Requirements

Interactive sync should remain responsive.

Target:

```text
8–12 recent messages
20–30 second time budget
Inbox only by default
Sent mailbox optional
Attachment sync optional
```

The sync result should report:

* Accounts attempted
* Messages fetched
* Inbound fetched
* Outbound fetched
* Imported
* Skipped
* Errors
* Triaged
* LLM required
* Store only
* Attachments saved
* Conversations updated
* Stopped early
* Elapsed seconds

The system must avoid:

* Full-table duplicate scans
* N+1 database queries
* Updating conversation status after every inserted message
* Parsing every attachment during quick sync
* Unbounded loops
* Silent errors
* Multiple simultaneous sync runs from repeated clicks

Conversation status updates should be batched after message insertion.

---

## 27. Database Requirements

The Operations Inbox should use:

* `order_intake` for incoming and outbound message records
* `order_intake_drafts` for pending booking drafts
* `loads` for dispatchable load records
* Communication records for load and case history
* Operations-case records for escalated work
* AI-feedback records for dispatcher corrections
* Attachment records for source documents

Database requirements:

* Unique source Message-ID
* Indexed conversation keys
* Indexed booking numbers
* Indexed container numbers
* Indexed reference numbers
* Nullable container quantity when unknown
* Unique child-load sequence per parent booking
* Transactions for multi-load creation
* Idempotent migrations
* Field provenance
* Parse errors
* Raw HTML storage
* Normalized body storage

Do not place raw migration SQL inside Streamlit page files.

---

## 28. Performance Requirements

The Operations Inbox initial page load should not:

* Parse attachments
* Run AI
* Load full history for every row
* Load candidate loads for every row
* Scan the entire email table

Expensive actions should run on demand.

Expected goals:

* Queue table loads quickly
* Selected work item loads independently
* History loads for the selected conversation
* AI runs only when requested
* Load matching runs only when requested
* Quick sync returns within its time budget

---

## 29. Error Handling Requirements

Do not use broad silent handling such as:

```python
except Exception:
    pass
```

for important operational workflows.

Errors should:

* Be logged
* Include enough context to diagnose
* Avoid exposing secrets
* Be shown to the user when action is required
* Preserve the work item in an unfinished state
* Avoid partial load creation where possible

Multi-row operations should use transactions.

---

## 30. Audit and Traceability

The system should preserve:

* Original message
* Full thread
* Parsed values
* Field sources
* Dispatcher corrections
* AI suggestions
* Final actions
* Outbound replies
* Created loads
* Updated loads
* Case status changes
* Completion timestamps

The dispatcher and manager should be able to understand:

```text
What was received
What the system extracted
What the system recommended
What the dispatcher changed
What action was taken
When the action occurred
```

---

## 31. Required Regression Scenario

The booking `RICGX1235800` must produce:

```text
Queue: New Orders
Request Type: New Booking
Department: Dispatch
Service Flow: Export
Booking Number: RICGX1235800
Reference Number: SO217089a/C25749C
Containers Required: 4
Container Size: 40HC
Known Container Number: blank
Customer: Continental Industries Group
Warehouse: PBP Packaging
Empty Pickup: ConGlobal-La Porte
Full Return: Bayport Terminal
Carrier: ONE
Vessel: CONTI CORTESIA 013W
```

It must not produce:

```text
Queue: Billing
Containers Required: 1
Service Flow: 25 KG BAGS...
Warehouse: PBP PACKAGING TRUCKER: CALI TRANS
Reference containing the rest of the email table
```

The dispatcher should be able to confirm creation of four container work-order placeholders.

A second creation attempt must create zero duplicate loads.

---

## 32. Definition of Done

The Operations Inbox meets the intended requirements when:

* New bookings route correctly.
* Actual billing requests still route correctly.
* Email HTML tables are preserved and parsed.
* Attachments are reviewed before draft readiness.
* Communication history is relevant and complete.
* Customer replies reopen the same conversation.
* Completed items leave active queues.
* Pending drafts retain latest agreed information.
* Dispatcher corrections are preserved.
* One booking can create multiple child loads safely.
* Unknown quantity is not silently treated as one.
* Quick sync is bounded and responsive.
* AI remains assistive and reviewable.
* All important actions are traceable.

````



