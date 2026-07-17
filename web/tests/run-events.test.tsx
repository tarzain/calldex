import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Badge } from "@/components/ui/badge";
import { MAX_RUN_EVENTS, mergeRunEvent, optimisticUserEvent, type RunEvent } from "@/lib/run-events";

function event(seq: number, type = "turn.output"): RunEvent {
  return { seq, run_id: "run-1", thread_id: "thread-1", turn_id: "turn-1", type, item_id: null, timestamp: seq, payload: {} };
}

describe("live run event state", () => {
  it("deduplicates replayed SSE events and bounds retained state", () => {
    let events: RunEvent[] = [];
    for (let seq = 1; seq <= MAX_RUN_EVENTS + 20; seq += 1) events = mergeRunEvent(events, event(seq));
    const replayed = mergeRunEvent(events, event(MAX_RUN_EVENTS + 20));
    expect(replayed).toBe(events);
    expect(events).toHaveLength(MAX_RUN_EVENTS);
    expect(events[0].seq).toBe(21);
  });

  it("reconciles an optimistic user message with the streamed start event", () => {
    const optimistic = optimisticUserEvent("run-1", "thread-1", "turn-1", "Fix the tests");
    const streamed = { ...event(1, "run.started"), payload: { prompt: "Fix the tests" } };
    const result = mergeRunEvent([optimistic], streamed);
    expect(result).toEqual([streamed]);
  });

  it("renders the running state with the shared shadcn primitive", () => {
    render(<Badge><span aria-hidden="true" />Running</Badge>);
    expect(screen.getByText("Running")).toBeVisible();
  });
});
