---
paths:
  - "pages_app/operations_inbox.py"
  - "services/operations_*.py"
  - "services/email_*.py"
  - "services/order_parser.py"
  - "ai_agents/document_parser_agent.py"
  - "ai_agents/operations_parser_agent.py"
---

#Operations Inbox Purpose

The Operations Inbox should manage operational work, not simply display email.

The intended workflow is:

Incoming email
→ preserve message metadata
→ normalize plain text and HTML
→ parse the email body and attachments
→ classify operational intent
→ determine the business conversation
→ match an existing load or maintain a pending order draft
→ show evidence and a recommended action
→ dispatcher reviews and acts
→ save communication and learning feedback
→ remove completed work from active queues
→ reopen the same conversation when the customer replies

Human review remains authoritative.

#Classification Precedence

Use this classification order consistently:

Spam / marketing / no-action archive
Quote request
Authoritative booking confirmation / new order
Appointment or PIN
Actual actionable billing request
POD or document request
Existing-load update
Needs review

Passive document language must not route a booking confirmation to Billing.

Examples of passive language:

Bill of lading
Ocean bill of lading
House bill of lading
Charges will be invoiced
Invoice contact
Rate sheet
This document is not an invoice

Billing requires an actionable request such as:

Please send the invoice
Correct the invoice
Invoice status
Billing dispute
Payment issue
Detention invoice
Demurrage invoice

The same classification result must be used by:

Email synchronization
Fast triage
Recheck Next Batch
Queue table
Dispatcher Decision
Pending draft
Learning feedback

Do not maintain separate conflicting queue rules in multiple functions.

#Multi-Container Booking Requirement

One customer booking may require multiple container moves.

Regression booking:

RICGX1235800

Expected values:

Customer: Continental Industries Group
Reference: SO217089a/C25749C
Service flow: Export
Containers required: 4
Container size: 40HC
Known physical container numbers: none initially
Commodity: Resin Non Haz
Cargo pickup warehouse: PBP Packaging
Empty pickup: ConGlobal-La Porte
Full return terminal: Bayport Terminal
Carrier: ONE
Vessel: CONTI CORTESIA 013W
Port of loading: Houston
Port of discharge: Kaohsiung

Correct database structure:

One parent booking or pending draft
→ container_qty = 4
→ container_size = 40HC
→ four child load placeholders
→ sequences 1 of 4 through 4 of 4
→ shared parent_booking_key = RICGX1235800
→ physical container numbers remain blank until known

Do not create four separate booking headers.

Do not treat RICGX1235800 as a physical container number.

Never silently default an unknown container quantity to 1.

If quantity is unknown, use:

NULL
Unknown
Needs Review

Multi-container child creation must be idempotent.

A repeated action must not create duplicate child loads.

Recommended uniqueness:

parent_booking_key + container_sequence
#Parsing Rules

Use this source precedence:

Dispatcher-confirmed value
> specialized document parser
> structured email-table parser
> validated generic parser
> LLM suggestion

A weaker parser source must never overwrite a stronger source.

Preserve:

Raw plain-text body
Raw HTML body
Normalized body text
HTML-table text
Attachments
Message-ID
In-Reply-To
References
Provider thread ID
Mailbox
Direction
Timestamps

Stable customer formats should use deterministic parser profiles.

Example:

continental_fcl_booking_v1

Store when practical:

Parser profile
Parser version
Confidence
Parse errors
Field provenance

Reject polluted values containing embedded labels such as:

CONTAINER QTY:
BAG TYPE:
TRUCKER:
BOOKING:
DOC CUT OFF:
PORT CUTOFF:

Only these service-flow values are valid:

Import
Export
Local Import
Local Export

Do not save arbitrary extracted text as service flow.

#Pending Order Draft Rules

When no load exists, maintain one pending order draft for the business conversation.

The draft must:

Merge information across customer replies
Preserve the latest agreed information
Preserve dispatcher-confirmed values
Track field provenance
Reject contaminated parser values
Remain active until order creation or cancellation
Support multiple future child loads
Allow physical container numbers to remain blank

Recommended draft states:

Awaiting Document Parse
Needs Details
Needs Dispatcher Review
Ready for Order Creation
Container Work Orders Created
Cancelled

A weaker parser must not overwrite dispatcher-confirmed data.

#Conversation and Work-Item Rules

Preserve:

Message-ID
In-Reply-To
References
Provider thread ID
Normalized subject
Booking number
Container number
Reference number
Business conversation key

Communication history must:

Include inbound and outbound messages
Be chronological
Include only the relevant business conversation
Allow the dispatcher to open the complete message
Avoid broad unrelated histories

After a dispatcher sends a reply and marks an item waiting on the customer:

Save the outbound message
Set the conversation to Waiting Customer
Remove the item from active work

When the customer replies:

Reopen the same conversation
Show the newest inbound request
Preserve the pending draft or matched load

Do not show every message in one thread as a separate active work item.

#Email Action Center Rules

Keep these functions together:

Reply / reply-all / forward
From
To
CC
Subject
AI draft assistance
Standard reply template
Editable body
Send action
Waiting-customer option
Success or error result

AI reply assistance belongs inside the email response section.

AI must not send email automatically.

#Email Sync Rules

The interactive Streamlit sync must remain bounded and responsive.

Target:

8–12 recent messages
20–30 second time budget
Inbox only by default
Sent mailbox optional
Attachment download optional

Quick sync should:

Fetch recent messages
Deduplicate by source Message-ID
Insert new messages
Collect touched conversation keys
Batch conversation-status updates
Return diagnostics
Stop cleanly when the time budget is reached

Do not deeply parse every attachment during quick sync.

Report:

Fetched
Imported
Skipped
Errors
Triaged
Threads updated
Stopped early

Avoid:

Full-table scans
N+1 database queries
Repeated status updates per message
Unbounded loops
Bare exception swallowing

#Required Reading

Before changing Operations Inbox behavior, read:

docs/OPERATIONS_INBOX_REQUIREMENTS.md
docs/MULTI_CONTAINER_BOOKING_SPEC.md
docs/CODE_REVIEW_PLAYBOOK.md
docs/TEST_MATRIX.md

Do not edit Operations Inbox code until the current implementation and relevant documentation have been reviewed.
