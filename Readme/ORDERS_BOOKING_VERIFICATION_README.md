# Orders Booking Verification Update

This update changes Orders tab #3 from Active Loads to Booking Verification.

New Orders workflow:

1. Intake Queue
2. Confirm Orders
3. Booking Verification

Booking Verification is the final office check before dispatch.

It shows:
- Bookings Awaiting Review
- Missing Information
- Awaiting Appointment
- Moved to Dispatch count
- Readiness %
- Missing Fields
- Verification Result

Actions:
- Mark Missing Info
- Awaiting Appointment
- Save Verification Note
- Move to Dispatch

Move to Dispatch sets Status = Ready to Dispatch and removes the booking from this tab.
It then appears on the Dispatch Board.

Replace only app.py and restart Streamlit.
