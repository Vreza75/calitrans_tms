// Mirrors realtime/channels.py's naming exactly (STEP 21: "Subscribe to
// the exact existing server contract" - not guessed). Keep these two
// modules in sync by hand; there is no cross-language codegen for this
// small a mapping.
const COLLECTION_CHANNEL_BY_AGGREGATE_TYPE: Record<string, string> = {
  load: "loads",
  order_intake_item: "inbox",
  dispatch_message: "communications",
  document: "documents",
};

const RESOURCE_CHANNEL_AGGREGATE_TYPES = new Set(["load"]);

export function collectionChannel(aggregateType: string): string {
  const channel = COLLECTION_CHANNEL_BY_AGGREGATE_TYPE[aggregateType];
  if (!channel) {
    throw new Error(`No known collection channel for aggregate_type "${aggregateType}".`);
  }
  return channel;
}

export function resourceChannel(aggregateType: string, aggregateId: string | number): string | null {
  if (!RESOURCE_CHANNEL_AGGREGATE_TYPES.has(aggregateType)) {
    return null;
  }
  return `${aggregateType}:${aggregateId}`;
}
