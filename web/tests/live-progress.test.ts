import { describe, expect, it } from "vitest";
import { normalizeLiveProgressText, type RunEvent } from "@/lib/run-events";

const event = (
  seq: number,
  type: string,
  payload: Record<string, unknown>,
  itemId: string | null = null,
): RunEvent => ({
  seq,
  run_id: "run-1",
  thread_id: "thread-1",
  turn_id: "turn-1",
  type,
  item_id: itemId,
  timestamp: seq,
  payload,
});

describe("live progress normalization", () => {
  it("replaces desktop commentary snapshots instead of repeating them", () => {
    const events = [
      event(1, "item.started", { item: { id: "message-1", type: "agentMessage", phase: "commentary", text: "I am checking" } }, "message-1"),
      event(2, "item.updated", { item: { id: "message-1", type: "agentMessage", phase: "commentary", text: "I am checking the runtime." } }, "message-1"),
    ];

    expect(normalizeLiveProgressText(events)).toBe("I am checking the runtime.");
  });

  it("continues to append SDK deltas and ignores final answers", () => {
    const events = [
      event(1, "item.agentMessage.delta", { delta: "Checking " }, "message-1"),
      event(2, "item.agentMessage.delta", { delta: "the runtime." }, "message-1"),
      event(3, "item.completed", { item: { id: "final-1", type: "agentMessage", phase: "finalAnswer", text: "Done." } }, "final-1"),
    ];

    expect(normalizeLiveProgressText(events)).toBe("Checking the runtime.");
  });

  it("preserves the first-seen order of separate progress messages", () => {
    const events = [
      event(1, "item.started", { item: { id: "message-1", type: "agentMessage", phase: "commentary", text: "First" } }, "message-1"),
      event(2, "item.started", { item: { id: "message-2", type: "agentMessage", phase: "commentary", text: "Second" } }, "message-2"),
      event(3, "item.updated", { item: { id: "message-1", type: "agentMessage", phase: "commentary", text: "First updated" } }, "message-1"),
    ];

    expect(normalizeLiveProgressText(events)).toBe("First updated\n\nSecond");
  });
});
