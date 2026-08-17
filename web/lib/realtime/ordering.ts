// STEP 23: event_id is the same-aggregate ordering token (see docs/
// architecture/REALTIME_EVENTS.md's "Wire payload" subsection - it is
// the domain_events row's primary key, monotonically increasing).
// Tracks the highest event_id applied per (aggregate_type, aggregate_id)
// and rejects anything not strictly newer, so a broadcast that arrives
// out of order (a genuine possibility - websocket delivery is not
// guaranteed FIFO across reconnects) cannot roll a client's view
// backward. This is a same-aggregate ordering guarantee only - there is
// no claim of a single global order across different resources.
export class EventOrderingTracker {
  private lastSeenEventId = new Map<string, number>();

  private key(aggregateType: string, aggregateId: string): string {
    return `${aggregateType}:${aggregateId}`;
  }

  isNew(aggregateType: string, aggregateId: string, eventId: number): boolean {
    const key = this.key(aggregateType, aggregateId);
    const last = this.lastSeenEventId.get(key);
    return last === undefined || eventId > last;
  }

  record(aggregateType: string, aggregateId: string, eventId: number): void {
    const key = this.key(aggregateType, aggregateId);
    const last = this.lastSeenEventId.get(key);
    if (last === undefined || eventId > last) {
      this.lastSeenEventId.set(key, eventId);
    }
  }
}
