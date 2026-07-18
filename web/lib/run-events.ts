export type RunEvent = {
  seq: number;
  run_id: string;
  thread_id: string;
  turn_id: string;
  type: string;
  item_id: string | null;
  timestamp: number;
  payload: Record<string, unknown>;
};

export const MAX_RUN_EVENTS = 500;

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

const text = (value: unknown) => typeof value === "string" ? value : "";

/**
 * Merge live progress text from both supported transports.
 *
 * The SDK sends incremental `agentMessage.delta`/reasoning events, while the
 * desktop follower sends the complete current `agentMessage` item on every
 * `item.started`/`item.updated` snapshot. Treating both as deltas repeats the
 * desktop text, so snapshots replace by item ID and true deltas append.
 */
export function normalizeLiveProgressText(events: RunEvent[]): string {
  const messages = new Map<string, { sequence: number; text: string }>();

  for (const event of events) {
    const payload = record(event.payload);
    const item = record(payload.item);
    const itemType = text(item.type);
    const phase = text(item.phase);
    const id = text(event.item_id || item.id || payload.itemId) || `stream:${event.type}`;

    if (
      event.type.startsWith("item.") &&
      itemType === "agentMessage" &&
      phase === "commentary"
    ) {
      const snapshot = text(item.text);
      if (snapshot) {
        messages.set(id, {
          sequence: messages.get(id)?.sequence ?? event.seq,
          text: snapshot,
        });
      }
      continue;
    }

    if (!event.type.includes("reasoning") && !event.type.includes("agentMessage.delta")) continue;
    const delta = text(payload.delta || payload.text);
    if (!delta) continue;
    const previous = messages.get(id);
    messages.set(id, {
      sequence: previous?.sequence ?? event.seq,
      text: `${previous?.text || ""}${delta}`,
    });
  }

  return [...messages.values()]
    .sort((a, b) => a.sequence - b.sequence)
    .map((message) => message.text)
    .join("\n\n");
}

export function optimisticUserEvent(runId: string, threadId: string, turnId: string, prompt: string): RunEvent {
  return {
    seq: -Date.now(),
    run_id: runId,
    thread_id: threadId,
    turn_id: turnId,
    type: "ui.user_message",
    item_id: null,
    timestamp: Date.now() / 1000,
    payload: { prompt },
  };
}

export function mergeRunEvent(current: RunEvent[], incoming: RunEvent): RunEvent[] {
  if (current.some((event) => event.seq === incoming.seq && event.run_id === incoming.run_id)) return current;
  const prompt = typeof incoming.payload.prompt === "string" ? incoming.payload.prompt : null;
  const reconciled = incoming.type === "run.started" || incoming.type === "run.steered"
    ? current.filter((event) => !(
        event.type === "ui.user_message" &&
        event.run_id === incoming.run_id &&
        event.payload.prompt === prompt
      ))
    : current;
  return [...reconciled, incoming].slice(-MAX_RUN_EVENTS);
}
