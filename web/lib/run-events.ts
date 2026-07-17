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
