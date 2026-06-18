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
