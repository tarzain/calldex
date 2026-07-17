import type { RunEvent } from "@/lib/run-events";

export type ToolActivityKind =
  | "command"
  | "fileChange"
  | "mcp"
  | "dynamic"
  | "collaboration"
  | "search"
  | "imageView"
  | "imageGeneration"
  | "unknown";

export type ToolActivity = {
  id: string;
  turnId: string;
  kind: ToolActivityKind;
  type: string;
  status: string;
  title: string;
  summary: string;
  sequence: number;
  command?: string;
  cwd?: string;
  durationMs?: number;
  exitCode?: number;
  input?: unknown;
  output?: string;
  error?: string;
  changes?: unknown[];
  paths?: string[];
  server?: string;
  tool?: string;
  progress?: string[];
};

export type ToolActivityGroup = {
  id: string;
  activities: ToolActivity[];
};

export type PersistedToolEvent = {
  id: string;
  turn_id: string;
  type: string;
  title: string;
  summary: string;
  status: string;
  details: Record<string, unknown>;
};

const OUTPUT_LIMIT = 12_000;
const TOOL_TYPES = new Set([
  "commandExecution",
  "fileChange",
  "mcpToolCall",
  "dynamicToolCall",
  "collabAgentToolCall",
  "webSearch",
  "imageView",
  "imageGeneration",
]);

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === "string" ? value : value == null ? "" : String(value);
const number = (value: unknown) => typeof value === "number" ? value : undefined;
const safeText = (value: unknown) => {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try { return JSON.stringify(value, null, 2) ?? String(value); } catch { return String(value); }
};

export function boundedOutput(value: string, limit = OUTPUT_LIMIT) {
  if (value.length <= limit) return value;
  return `… [${value.length - limit} earlier characters omitted]\n${value.slice(-limit)}`;
}

export function isToolEventType(type: string) {
  return TOOL_TYPES.has(type) || type.includes("ToolCall") || type.includes("commandExecution") || type.includes("fileChange");
}

function kindFor(type: string): ToolActivityKind {
  if (type.includes("commandExecution")) return "command";
  if (type.includes("fileChange")) return "fileChange";
  if (type.includes("mcpToolCall")) return "mcp";
  if (type.includes("dynamicToolCall")) return "dynamic";
  if (type.includes("collabAgentToolCall")) return "collaboration";
  if (type.includes("webSearch")) return "search";
  if (type.includes("imageGeneration")) return "imageGeneration";
  if (type.includes("imageView")) return "imageView";
  return "unknown";
}

function pathsFromChanges(changes: unknown[]) {
  return changes.map((change) => {
    const value = record(change);
    return text(value.path || value.file || value.filePath);
  }).filter(Boolean);
}

function fromItem(item: Record<string, unknown>, fallback: Partial<ToolActivity>): ToolActivity {
  const type = text(item.type || fallback.type || "unknownTool");
  const kind = kindFor(type);
  const changes = Array.isArray(item.changes) ? item.changes : undefined;
  const tool = text(item.tool || item.name);
  const server = text(item.server || item.namespace);
  const command = text(item.command);
  const path = text(item.path);
  const status = text(item.status || fallback.status || "completed");
  const output = safeText(item.aggregatedOutput || item.output || item.result || item.contentItems);
  const errorValue = record(item.error);
  const error = safeText(errorValue.message || item.error);
  const title = kind === "command" ? command || "Command"
    : kind === "fileChange" ? `${changes?.length || 0} file change${changes?.length === 1 ? "" : "s"}`
    : kind === "mcp" ? [server, tool].filter(Boolean).join(" / ") || "MCP tool"
    : kind === "dynamic" ? [server, tool].filter(Boolean).join(" / ") || "Tool call"
    : kind === "collaboration" ? tool || "Agent collaboration"
    : kind === "search" ? text(item.query) || "Web search"
    : kind === "imageGeneration" ? "Generated image"
    : kind === "imageView" ? path || "Viewed image"
    : text(fallback.title) || tool || type;
  return {
    id: text(item.id || fallback.id),
    turnId: text(fallback.turnId),
    kind,
    type,
    status,
    title,
    summary: text(fallback.summary) || title,
    sequence: fallback.sequence || 0,
    command: command || undefined,
    cwd: text(item.cwd) || undefined,
    durationMs: number(item.durationMs),
    exitCode: number(item.exitCode),
    input: item.arguments ?? item.input ?? (kind === "search" ? item.action : kind === "collaboration" ? {
      operation: tool,
      targets: item.receiverThreadIds || item.agentsStates,
      prompt: item.prompt,
    } : undefined),
    output: output ? boundedOutput(output) : undefined,
    error: error || undefined,
    changes,
    paths: changes ? pathsFromChanges(changes) : path ? [path] : undefined,
    server: server || undefined,
    tool: tool || undefined,
    progress: [],
  };
}

export function normalizePersistedTool(event: PersistedToolEvent, sequence = 0): ToolActivity | null {
  if (!isToolEventType(event.type)) return null;
  return fromItem({ ...event.details, type: event.type, id: event.id }, {
    id: event.id,
    turnId: event.turn_id,
    type: event.type,
    status: event.status,
    title: event.title,
    summary: event.summary,
    sequence,
  });
}

export function normalizeLiveTools(events: RunEvent[]): ToolActivity[] {
  const activities = new Map<string, ToolActivity>();
  for (const event of events) {
    const payload = record(event.payload);
    const item = record(payload.item);
    const itemType = text(item.type);
    const id = text(event.item_id || item.id || payload.itemId);
    if (itemType && isToolEventType(itemType)) {
      const previous = activities.get(id);
      const next = fromItem(item, {
        id,
        turnId: event.turn_id,
        type: itemType,
        status: text(item.status) || (event.type.endsWith("started") ? "inProgress" : "completed"),
        sequence: previous?.sequence ?? event.seq,
      });
      activities.set(id, {
        ...previous,
        ...next,
        input: next.input ?? previous?.input,
        output: next.output ?? previous?.output,
        error: next.error ?? previous?.error,
        progress: previous?.progress || [],
      });
      continue;
    }
    const current = activities.get(id);
    if (!current) continue;
    if (event.type.includes("outputDelta")) {
      current.output = boundedOutput(`${current.output || ""}${text(payload.delta)}`);
    } else if (event.type.includes("progress")) {
      const message = text(payload.message || payload.delta);
      if (message && current.progress?.at(-1) !== message) current.progress = [...(current.progress || []), message].slice(-20);
    }
  }
  return [...activities.values()].sort((a, b) => a.sequence - b.sequence);
}

function phrase(activity: ToolActivity, active: boolean) {
  const readLike = /read|fetch|get|open/i.test(`${activity.tool || ""} ${activity.title}`);
  if (activity.kind === "command") return active ? "Running commands" : "Ran commands";
  if (activity.kind === "fileChange") return active ? "Editing files" : "Edited files";
  if ((activity.kind === "mcp" || activity.kind === "dynamic") && readLike) return active ? "Reading files" : "Read files";
  if (activity.kind === "search") return active ? "Searching" : "Searched";
  if (activity.kind === "collaboration") return active ? "Delegating work" : "Delegated work";
  if (activity.kind === "imageView") return active ? "Viewing images" : "Viewed images";
  if (activity.kind === "imageGeneration") return active ? "Generating images" : "Generated images";
  return active ? "Using tools" : "Used tools";
}

export function summarizeToolActivities(activities: ToolActivity[]) {
  const categories = new Map<string, { activity: ToolActivity; active: boolean }>();
  for (const activity of activities) {
    const key = phrase(activity, false);
    const active = activity.status === "inProgress" || activity.status === "running";
    const existing = categories.get(key);
    if (existing) existing.active ||= active;
    else categories.set(key, { activity, active });
  }
  const values = [...categories.values()].map(({ activity, active }) => phrase(activity, active));
  return values.map((value, index) => index === 0 ? value : `${value[0].toLowerCase()}${value.slice(1)}`).join(", ");
}

export function groupConsecutiveTools<T>(items: T[], isTool: (item: T) => boolean, toActivity: (item: T, index: number) => ToolActivity | null) {
  const groups: Array<{ kind: "item"; item: T } | { kind: "tools"; group: ToolActivityGroup }> = [];
  let current: ToolActivity[] = [];
  const flush = () => {
    if (!current.length) return;
    groups.push({ kind: "tools", group: { id: current.map((item) => item.id).join(":"), activities: current } });
    current = [];
  };
  items.forEach((item, index) => {
    if (isTool(item)) {
      const activity = toActivity(item, index);
      if (activity) current.push(activity);
    } else {
      flush();
      groups.push({ kind: "item", item });
    }
  });
  flush();
  return groups;
}
