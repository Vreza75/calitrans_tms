# Operations Inbox and Order Editor Change Record

Date: June 18, 2026

## Summary

This update makes the CaliTrans TMS easier to use for daily dispatch work by improving the Operations Inbox, adding client email send/receive workflow, and making the order editor clear when moving between order queues or load types.

## What Changed

### Operations Inbox

- Added a `Check Client Email` action that imports recent client messages into Operations Inbox.
- Expanded email detection beyond load orders to include:
  - Quote and rate requests
  - Missing information requests
  - Load and booking updates
  - Appointment updates
  - POD requests
  - Cancellation requests
- Added automatic classification for `Missing Information`.
- Added inbox counters for open requests, quote requests, missing information, and waiting-on-customer items.
- Added inbox tabs for `Missing Info` and `Waiting`.
- Added customer reply composer directly inside the request review panel.
- Added outgoing email logging to `operations_email_replies`.
- Added outbound reply history to `load_communications` when the request is matched to an existing load.
- Added the option to mark a request `Waiting on Customer` after sending a reply.

### Order Editor

- Moved the Order Detail Editor inside the selected order queue/type view.
- Selecting an order row now opens the editor directly under that table.
- Switching to another queue or load type clears the previous editor so stale order details are not left on screen.
- Added a `Clear Editor` button.
- Added quick actions to the order editor:
  - Mark Missing Info
  - Move To Dispatch
  - Cancel Order
- Removed the extra `Open Order` button to reduce clicks.

### Cleanup

- Removed the old global `Test Email` button from the main app flow.

## Files Changed

- `app.py`
- `email_client.py`
- `database/operations_email_workflow_migration.sql`
- `OPERATIONS_INBOX_ORDER_EDITOR_CHANGE_RECORD.md`

## Database Step Required

Run this SQL file in Supabase SQL Editor:

```text
database/operations_email_workflow_migration.sql
```

Run it after:

```text
database/schema.sql
database/portpro_style_migration.sql
database/order_intake_migration.sql
```

## Email Settings Needed

Incoming email uses:

```text
EMAIL_ADDRESS
EMAIL_APP_PASSWORD
EMAIL_IMAP_SERVER
EMAIL_INBOX_FOLDER
EMAIL_OPERATIONS_IMAP_SEARCH
EMAIL_OPERATIONS_TERMS
```

Outgoing email uses:

```text
SMTP_HOST
SMTP_PORT
SMTP_USER
SMTP_PASSWORD
DISPATCH_EMAIL
COMPANY_NAME
```

`EMAIL_INBOX_FOLDER`, `EMAIL_OPERATIONS_IMAP_SEARCH`, `EMAIL_OPERATIONS_TERMS`, `DISPATCH_EMAIL`, and `COMPANY_NAME` are optional. The app has defaults for them.

## Notes

- Client emails are deduped by message id when available.
- If an imported email matches an existing booking, container, or reference number, the app links it to that load.
- Sent replies are logged even when delivery fails, so failed email attempts can be reviewed.

## June 22, 2026 Yahoo Inbox Fix

- Updated Yahoo IMAP handling to use `imap.mail.yahoo.com`, port `993`, and `INBOX` by default.
- Added fallback inbox folder checks for `INBOX`, `Inbox`, and `inbox`.
- Increased the Yahoo inbox scan window so older recent emails are less likely to be missed.
- Changed Operations Inbox import so recent inbox emails are not dropped just because they do not contain quote/load keywords.
- Kept keyword matches as metadata, but classification and tabs now happen inside Operations Inbox.
- Added a visible import result after `Check Client Email`, showing fetched, imported, and skipped counts.
- Updated outbound email to work when only `YAHOO_EMAIL` and `YAHOO_APP_PASSWORD` are configured.
- Reworked `email_ingest.py` into a safe Yahoo inbox diagnostic that uses the same email client as the app.

## June 22, 2026 Intelligent Inbox Update

- Tightened Operations Inbox classification so vague customer update requests without a booking, container, or reference stay in `Customer Request` instead of becoming new bookings, booking updates, or quote requests.
- Added a `Needs Details` queue for messages that need a first-response request for identifying load details.
- Split `POD Requests` into their own queue instead of mixing them into general customer requests.
- Added more specific action guidance for update, quote, order, appointment, POD, cancellation, and customer-detail requests.
- Improved reply drafts so vague update requests ask for booking/container/reference details, quote requests ask for lane/equipment/date details, and matched operational requests get a more relevant acknowledgment.
- Disabled `Create New Order` and `Create Quote` when the selected email does not have enough detail for that action.
- Added an Operations Inbox process-feedback panel with recommended team workflow improvements.

## June 22, 2026 Phase 1 AI Assist

- Added optional OpenAI-powered AI Assist for Operations Inbox review.
- AI Assist suggests request type, confidence, priority, missing details, next action, and an editable customer reply draft.
- Added `Apply AI Classification` so dispatch can accept the AI classification after review.
- Kept all AI email replies human-approved; AI drafts do not send email automatically.
- Added optional import-time AI classification behind `OPERATIONS_AI_AUTO_CLASSIFY=true`.
- New settings:
  - `OPENAI_API_KEY`
  - `OPERATIONS_AI_MODEL` optional, defaults to `gpt-5.5`
  - `OPERATIONS_AI_REASONING_EFFORT` optional, defaults to `low`
  - `OPERATIONS_AI_AUTO_CLASSIFY` optional, defaults to `false`

## June 22, 2026 Phase 2 AI Load Matching

- Added customer-safe load context for AI Assist, including load status, route, LFD, delivery need date, appointment/ETA fields, current location, live load/unload status, and POD/document availability.
- Added AI load candidates so AI can suggest a matched load only from real candidate records.
- Added guarded AI matched-load application: the app accepts an AI suggested load only when the ID exists in the candidate list shown to dispatch.
- Preserved dispatcher-approved matched loads on refresh, even when the original email did not include booking, container, or reference details.
- Improved AI prompts so matched emails can receive status-aware reply drafts while unmatched emails ask for identifying details.
- Kept billing, carrier-pay, and internal-note details out of the AI context used for customer replies.

## June 23, 2026 Phase 3 AI Feedback Learning

- Added `operations_ai_feedback` to store dispatcher feedback from AI-assisted inbox work.
- Saved feedback when dispatch accepts AI classification, manually corrects AI classification/action guidance, or sends an edited AI reply draft.
- Added optional `Learning Notes` in AI Assist so dispatch can explain what AI got right or wrong.
- Fed recent dispatcher feedback examples back into AI Assist prompts so future suggestions can follow team corrections and preferred reply style.
- Kept learning as a review-time prompt feedback loop rather than automatic model training.
- Added safe on-demand table creation so feedback logging can start even before the migration is rerun.

## June 23, 2026 Inbox Refresh Fix

- Optimized open inbox auto-classification so it only rechecks rows that actually need classification or low-confidence correction.
- Avoided reloading the inbox table after auto-classification unless a row was changed.
- Added a clearer empty-state explanation when Yahoo fetches duplicate emails but no open requests are visible because existing records are already closed, attached, converted, or filtered.
- Added saved operations email status counts to help diagnose why fetched emails are not appearing in the open inbox.

## June 23, 2026 Bilingual Operations Inbox

- Added Spanish and English language handling for Operations Inbox customer replies.
- Added a `Reply Language` selector with `Auto`, `English`, `Spanish`, and `Bilingual` options.
- Added Spanish rule-based reply templates for customer requests, quote requests, booking updates, appointment updates, missing information, cancellations, POD requests, and new bookings.
- Updated AI Assist so generated reply drafts follow the selected response language.
- Expanded inbox classification keywords so Spanish customer emails route into the correct request queues.

## June 23, 2026 Operations Inbox Performance and PDF Intake

- Replaced the multi-tab inbox rendering path with a single active queue selector so selecting or opening an email no longer renders every queue table.
- Limited automatic reclassification to rows that are missing classification or need a low-confidence correction.
- Used saved classification data for normal review opens, and moved load/AI match context behind an on-demand button.
- Saved PDF attachments from Operations email imports into local document storage with parsed field metadata.
- Added an Operations Inbox PDF panel to select, view, download, parse, compare email-vs-PDF fields, save PDF data to the request, create a load from a PDF, attach a PDF to a matched load, or update matched load fields from PDF data.
- Added database indexes for Operations Inbox review filters and received-date sorting.
- Backfilled missing PDF attachments onto already-imported Operations Inbox records when Yahoo returns duplicate/skipped emails.
- Added manual PDF upload/import inside an Operations request.
- Added movable Operations Inbox queue preferences so dispatch can move preferred queues earlier and save that order.
- Carried parsed PDF fields such as address, document cutoff, and size into load creation and update flows.

## June 23, 2026 Port Houston Integration

- Added an all-in-one `Port Houston Integration` app section for Navis EVP data.
- Added secure credential handling through local environment variables or Streamlit secrets; no Port Houston credentials are stored in source code.
- Added live lookup tools for inventory units, bookings, vessel visits, gate appointments, appointment time slots, gate transactions, truck visits, and service events.
- Added selected-load lookup and sync actions so dispatch can pull Port Houston container or booking data into load notes and safe missing fields.
- Added appointment payload generation for create/update/cancel appointment workflows.
- Added event subscription tools for Navis EVP notify subscribers and a drayage data mapping guide.
- Added a Port Houston sync log migration for audit history of lookups and updates.

## June 23, 2026 Operations Inbox Tab Rollback and Speed Pass

- Restored Operations Inbox queues to the standard tab row instead of the queue dropdown and tab preference panel.
- Added count labels to each queue tab so dispatch can see volume before opening a queue.
- Split the inbox list query from the full email-detail query so routine queue loading uses a short body preview and full email content loads only after opening a request.
- Moved heavy smart regrouping behind the `Recheck Groups` button so normal row selection and request opening do not reclassify older messages on every click.
- Cleared cached inbox data after classification changes so tab counts and open request details refresh immediately after dispatcher actions.
- Batched duplicate checks during `Check Client Email` so already-imported Yahoo messages are matched against saved inbox records without one database lookup per email.

## June 23, 2026 Full TMS Speed and Flow Pass

- Deferred full load-board data loading until the selected section actually needs load data, so Operations Inbox and Email Imports open faster.
- Cached the large header image conversion and full prepared TMS load dataset to reduce repeated work on normal reruns.
- Fixed extended PortPro-style load fields so real terminal, ETA, appointment, vessel, and live-status values merge cleanly instead of producing duplicate blank columns.
- Narrowed load search to operational fields instead of scanning every dataframe column row by row.
- Added a focused Dispatch Board renderer with one `Dispatch View` and one `Load Type` active at a time, matching a dispatcher workflow and avoiding rendering all boards at once.
- Moved Operations Inbox PDF-count calculation into the database list query and removed the row-by-row JSON parsing from page rendering.
- Removed unused Operations Inbox tab preference helpers from the active app code after returning to normal tabs.

## June 25, 2026 Phase 0 Email Synchronization Engine

- Added Inbox plus Sent synchronization for Operations Inbox so inbound and outbound customer communication can be tracked together.
- Captured email direction, mailbox, normalized Message-ID, In-Reply-To, References, thread ID, timestamps, and PDF attachments during sync.
- Added sync-level deduplication by Message-ID and fallback subject/sender/timestamp matching.
- Imported outbound Sent messages as closed synchronized records so they support conversation history without creating new open requests.
- Added Operations Inbox sync metrics for synced Inbox messages, synced Sent messages, thread count, and last sync time.
- Added an Email Synchronization Metadata panel on each request review.
- Added migration columns and indexes for email direction, mailbox, thread IDs, references, and sync timestamps.

## June 25, 2026 Epic 0.2 Conversation Threads

- Added normalized subject handling so `Re:`, `FW:`, `Fwd:`, extra spacing, and casing differences do not break thread matching.
- Added conversation keys that prefer References, then In-Reply-To, then Message-ID, then normalized subject.
- Added conversation status tracking with `New Conversation`, `Waiting Dispatcher`, `Waiting Customer`, and `Answered Outside TMS`.
- Made inbound and outbound sync inherit existing thread context, matched load, and request type when replies arrive in the same conversation.
- Added latest direction, reply status, and message count columns to Operations Inbox queues.
- Added a chronological `Conversation Timeline` panel to request review with inbound/outbound messages in one place.
- Inserted dispatcher replies sent from the TMS back into the same thread timeline so internal TMS replies and outside Sent-folder replies are both visible.
- Added migration columns and indexes for normalized subject and conversation status.

## June 25, 2026 Epic 0.3 Operations Cases

- Added `operations_cases` and `operations_case_notes` so email conversations become trackable Operations Cases.
- Added readable case numbers such as `CASE-2026-0001`.
- Linked Operations Inbox records, sent replies, load communications, PDF actions, and internal notes back to the same case.
- Automatically creates or updates a case during Inbox/Sent email sync.
- Reopens a closed case when a new inbound customer reply arrives.
- Added case status, owner, priority, linked load, next action, and message count tracking.
- Added Operations Case dashboard metrics for open cases, waiting dispatch, waiting customer, and closed cases.
- Added case number, case status, owner, and priority columns to Operations Inbox queues.
- Added an Operations Case panel inside request review with quick actions: save case, waiting customer, waiting dispatcher, close, reopen, add internal note, and merge duplicate case.
- Added a case timeline that combines emails, internal notes, status changes, and load actions.
- Updated PDF create/attach/update actions so document-driven load work updates the linked case.
- Added migration columns and indexes for case linking across `order_intake`, `load_communications`, and `operations_email_replies`.

## June 25, 2026 Epic 0.4-0.13 Operations Intelligence Expansion

- Added weighted intent scoring for Operations Inbox classification instead of keyword-only routing.
- Expanded supported intent categories to Billing, Driver Issue, Port Issue, and Spam/Marketing.
- Added top intent-score context to the AI Assist rule payload.
- Added scored load-match candidates using booking number, container number, reference number, load ID, customer/date, and vessel.
- Added dispatcher accept/reject controls for suggested load matches.
- Added automatic owner suggestions, manual owner queue filtering, and ownership history.
- Added Operations Case activity events for viewed, assigned, replied, status change, closed, and synced email actions.
- Added SLA fields for first response, resolution, customer wait, department wait, and SLA status.
- Added SLA status refresh and dashboard visibility for warning/overdue cases.
- Added tone selection for smart replies: Professional, Concise, Friendly, and Apology / Delay.
- Added reply templates for Billing, Driver Issue, Port Issue, and Spam/Marketing in English and Spanish.
- Added a Communication Dashboard section inside Dashboard with open cases, waiting by department, average first response, cases closed, SLA compliance, owner workload, SLA watch, and shared case view.
- Added migration tables for `operations_case_owner_history` and `operations_case_events`.
- Added migration backfill for first-response and resolution SLA due dates on existing cases.

## June 25, 2026 Operations Inbox Attachment Verification and Parsing

- Updated email synchronization to capture all decoded email attachments instead of only filenames ending in `.pdf`.
- Saved attachment metadata including filename, content type, size, file path, parse result, extracted text preview, and parsed order fields.
- Preserved the existing PDF workflow while adding support for saved DOCX, TXT, CSV, and image attachments.
- Updated duplicate/skipped email backfill so existing inbox records can receive newly discovered attachments when Yahoo returns the message again.
- Updated the Inbox document panel to show all saved attachments, download them, preview PDFs/images, and parse supported documents for order fields.
- Added manual upload for PDF, DOCX, TXT, CSV, PNG, JPG, and JPEG inside an Operations Inbox request.
- Improved email body parsing for drayage requests with unlabeled references, `1 x40` size text, hazmat/IMO notes, loading-ready dates, and cutoff dates.
- Added `attachment_count` beside `pdf_count` in Operations Inbox queues.

## June 25, 2026 Attachment Recovery and Corrected-Container Parsing

- Added a targeted `Rescan This Email for Attachments` action inside each Operations Inbox request when the email body mentions attached documents but no files are saved.
- Made the rescan look up the exact Message-ID first, then fall back to the broader Operations mailbox scan if needed.
- Added attachment handling for nested/forwarded email structures while preventing duplicate saved files.
- Auto-reparses the email body when opening a request and saves improved fields back to the inbox record.
- Updated parser logic for customer corrections such as `container TGCU5422440 instead of ECMU5364722`, so the corrected container is used for load matching and order creation.
- Extracts reference numbers from subjects like `129693074 / ECMU5364722` and recognizes Flat World / `@flatworldgs.com` as `Flat World Global Logistics`.

## June 25, 2026 Operations Inbox Flow Cleanup

- Added a visible `attachment_status` queue column showing `Saved`, `Mailbox`, `Mentioned`, or `None` so dispatch can audit attachment handling without opening each request.
- Tightened New Booking classification so informational replies such as hours, FYI notes, corrections, and pre-alert updates route to Booking Updates or Customer Requests unless they include clear order-placement language.
- Updated `Recheck Groups` so obvious informational New Booking rows can be corrected even when they previously had higher confidence.
- Filtered Conversation Timeline rows by booking, container, reference, matched load, or topic tokens so broad threads do not mix unrelated orders.
- Simplified the document panel to review/import/attach documents only; order creation and updates now happen in the single `Order / Quote Actions` area.
- Updated `Update Existing Order` to attach the email and optionally fill blank order fields from parsed email/document data.

## June 25, 2026 Operations Inbox UI and Query Fix

- Fixed the Operations Inbox load error caused by an unescaped `_email_sync` JSON path in the inbox query.
- Reworked the Operations Inbox header, sync notice, and KPI metrics into a cleaner dispatch-console layout.
- Normalized fonts, button sizes, tab sizing, expander borders, dataframe borders, and metric card sizing for a more professional TMS feel.
- Removed the old negative banner margin and reduced oversized rounded/shadowed styling that made the page feel cramped.

## June 26, 2026 Operations Manager Queue Workflow

- Replaced the long email-type tab list with seven dispatcher action queues: `Action Required`, `New Orders`, `Existing Loads`, `Waiting`, `Documents`, `Billing`, and `Review`.
- Moved Missing Info into a status label instead of a standalone tab, grouped POD/document work under `Documents`, grouped driver/port work under `Existing Loads`, and auto-closes Spam/Marketing during sync or regrouping.
- Added stronger inbox filters for search, owner, priority, status, request type, and attachment status.
- Added normalized queue columns for owner, priority, status, attachment state, case number, reply status, reference, matched load, confidence, and action guidance.
- Updated classification learning so manual dispatcher saves are stored even when AI Assist is not used, and future routing can apply recent corrections by sender/topic pattern.

## June 26, 2026 Case-Centric Operations Workspace

- Strengthened Operations Case matching so copied or related emails can attach to the same open case by linked load, booking, container, reference, normalized subject, thread/conversation key, and sender domain.
- Collapsed Operations Inbox display to one visible row per Operations Case instead of one row per copied email, while still preserving every email inside the case timeline.
- Added case perspectives and manager focus filters for Dispatch, Operations Manager, Billing, Customer Service, High Priority, Escalated/Overdue, Waiting over 24 hours, and Unassigned cases.
- Added a case-first review header showing case number, customer, booking, container, linked load, priority, owner, status, last reply, last customer email, total messages, and SLA timing.
- Changed the review timeline to show the full Operations Case conversation and activity stream, including inbound emails, synced replies, internal notes, load actions, and system events.
- Added custom owner support and quick waiting statuses for Manager, Driver, Port, and Warehouse so cases can show who or what the operation is waiting on.
