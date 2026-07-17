import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolActivityGroup } from "@/components/calldex/tool-activity";
import {
  boundedOutput,
  groupConsecutiveTools,
  normalizeLiveTools,
  normalizePersistedTool,
  summarizeToolActivities,
  type PersistedToolEvent,
} from "@/lib/tool-activity";
import type { RunEvent } from "@/lib/run-events";

const persisted = (overrides: Partial<PersistedToolEvent> = {}): PersistedToolEvent => ({
  id: "command-1",
  turn_id: "turn-1",
  type: "commandExecution",
  title: "Command",
  summary: "npm test",
  status: "completed",
  details: { type: "commandExecution", command: "npm test", cwd: "/workspace", exitCode: 0, aggregatedOutput: "passed" },
  ...overrides,
});

const live = (seq: number, type: string, payload: Record<string, unknown>, itemId = "command-1"): RunEvent => ({
  seq,
  run_id: "run-1",
  thread_id: "thread-1",
  turn_id: "turn-1",
  type,
  item_id: itemId,
  timestamp: seq,
  payload,
});

describe("tool activity normalization", () => {
  it("normalizes persisted commands and semantic group summaries", () => {
    const command = normalizePersistedTool(persisted(), 1)!;
    const edit = normalizePersistedTool(persisted({
      id: "edit-1",
      type: "fileChange",
      title: "File change",
      details: { type: "fileChange", changes: [{ path: "app.tsx", kind: "update", diff: "+hello" }] },
    }), 2)!;
    expect(command).toMatchObject({ kind: "command", command: "npm test", cwd: "/workspace", exitCode: 0 });
    expect(edit.paths).toEqual(["app.tsx"]);
    expect(summarizeToolActivities([edit, command])).toBe("Edited files, ran commands");
  });

  it("aggregates started, deltas, progress, and completion by item ID", () => {
    const events = [
      live(1, "item.started", { item: { id: "command-1", type: "commandExecution", command: "npm test", status: "inProgress" } }),
      live(2, "item.commandExecution.outputDelta", { itemId: "command-1", delta: "one\n" }),
      live(3, "item.commandExecution.outputDelta", { itemId: "command-1", delta: "two\n" }),
      live(4, "item.completed", { item: { id: "command-1", type: "commandExecution", command: "npm test", status: "completed", exitCode: 0 } }),
    ];
    expect(normalizeLiveTools(events)).toEqual([expect.objectContaining({
      id: "command-1",
      status: "completed",
      output: "one\ntwo\n",
      exitCode: 0,
    })]);
  });

  it("bounds output, deduplicates progress, and preserves grouping boundaries", () => {
    expect(boundedOutput("x".repeat(20), 10)).toContain("earlier characters omitted");
    const mcp = normalizeLiveTools([
      live(1, "item.started", { item: { id: "mcp-1", type: "mcpToolCall", server: "files", tool: "read_file", status: "inProgress" } }, "mcp-1"),
      live(2, "item.mcpToolCall.progress", { itemId: "mcp-1", message: "Reading" }, "mcp-1"),
      live(3, "item.mcpToolCall.progress", { itemId: "mcp-1", message: "Reading" }, "mcp-1"),
    ])[0];
    expect(mcp.progress).toEqual(["Reading"]);
    const grouped = groupConsecutiveTools(["tool-a", "tool-b", "comment", "tool-c"], (item) => item.startsWith("tool"), (_, index) => ({ ...mcp, id: String(index) }));
    expect(grouped.map((entry) => entry.kind)).toEqual(["tools", "item", "tools"]);
  });

  it("renders MCP results, failures, collaboration, images, and unknown tools as safe values", () => {
    const values = [
      persisted({ id: "mcp", type: "mcpToolCall", details: { type: "mcpToolCall", server: "github", tool: "get_issue", result: { title: "Issue" }, status: "completed" } }),
      persisted({ id: "failed", type: "dynamicToolCall", details: { type: "dynamicToolCall", namespace: "local", tool: "write", error: { message: "Denied" }, status: "failed" } }),
      persisted({ id: "collab", type: "collabAgentToolCall", details: { type: "collabAgentToolCall", tool: "spawn", prompt: "Check tests", receiverThreadIds: ["agent-1"] } }),
      persisted({ id: "image", type: "imageView", details: { type: "imageView", path: "/tmp/image.png" } }),
      persisted({ id: "unknown", type: "customToolCall", details: { type: "customToolCall", tool: "mystery", arguments: { html: "<script>no</script>" } } }),
    ].map((event, index) => normalizePersistedTool(event, index)!);
    expect(values.map((value) => value.kind)).toEqual(["mcp", "dynamic", "collaboration", "imageView", "unknown"]);
    expect(values[0].output).toContain('"title": "Issue"');
    expect(values[1].error).toBe("Denied");
    expect(values[2].input).toMatchObject({ prompt: "Check tests", targets: ["agent-1"] });
    expect(values[4].input).toEqual({ html: "<script>no</script>" });
  });
});

describe("ToolActivityGroup", () => {
  it("is collapsed initially and expands chronological, safe details", () => {
    const command = normalizePersistedTool(persisted(), 1)!;
    const search = normalizePersistedTool(persisted({
      id: "search-1",
      type: "webSearch",
      title: "Web search",
      details: { type: "webSearch", query: "<b>safe query</b>", action: { type: "search" } },
    }), 2)!;
    render(<ToolActivityGroup group={{ id: "group", activities: [command, search] }} />);
    const trigger = screen.getByRole("button", { name: "Expand tool activity" });
    expect(screen.queryByText("Ran npm test")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    const rows = screen.getAllByRole("button", { name: /Expand/ });
    expect(rows[0]).toHaveTextContent("Ran npm test");
    expect(rows[1]).toHaveTextContent("<b>safe query</b>");
    expect(document.querySelector("b")).toBeNull();
  });
});
