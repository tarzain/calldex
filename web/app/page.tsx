"use client";

import {
  RoomAudioRenderer,
  SessionProvider,
  StartAudio,
  useAgent,
  useSession,
  useSessionMessages,
} from "@livekit/components-react";
import "@livekit/components-styles";
import {
  Activity,
  Archive,
  Bot,
  Braces,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Copy,
  Database,
  Folder,
  FolderGit2,
  GitFork,
  Menu,
  Mic,
  MicOff,
  Monitor,
  Moon,
  MoreHorizontal,
  Pencil,
  Plus,
  PhoneOff,
  RefreshCw,
  Search,
  Sparkles,
  ShieldCheck,
  Sun,
  SunMoon,
  TerminalSquare,
  UserRound,
  Wrench,
  X,
} from "lucide-react";
import { ConnectionState, TokenSource } from "livekit-client";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Persona, type PersonaState } from "@/components/ai-elements/persona";
import {
  Conversation,
  ConversationContent,
  ConversationDownload,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import {
  Message,
  MessageAction,
  MessageActions,
  MessageContent,
  MessageResponse,
} from "@/components/ai-elements/message";
import type { UIMessage } from "ai";
import {
  PromptInput,
  PromptInputFooter,
  PromptInputSelect,
  PromptInputSelectContent,
  PromptInputSelectItem,
  PromptInputSelectTrigger,
  PromptInputSubmit,
  PromptInputTextarea,
} from "@/components/ai-elements/prompt-input";
import { Reasoning, ReasoningContent, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import { Plan, PlanContent, PlanHeader, PlanTitle, PlanTrigger } from "@/components/ai-elements/plan";
import { ToolActivityGroup } from "@/components/calldex/tool-activity";
import {
  Confirmation,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRequest,
  ConfirmationTitle,
} from "@/components/ai-elements/confirmation";
import { mergeRunEvent, optimisticUserEvent, type RunEvent } from "@/lib/run-events";
import {
  groupConsecutiveTools,
  isToolEventType,
  normalizeLiveTools,
  normalizePersistedTool,
} from "@/lib/tool-activity";

type ThreadSummary = {
  id: string;
  name: string;
  preview: string;
  repository_path: string;
  status: string;
  created_at: string | null;
  updated_at: string | null;
};

type TimelineEvent = {
  id: string;
  turn_id: string;
  type: string;
  title: string;
  summary: string;
  status: string;
  phase: string | null;
  timestamp: string | null;
  started_at: string | null;
  completed_at: string | null;
  details: Record<string, unknown>;
};

type ThreadDetail = {
  thread: ThreadSummary;
  events: TimelineEvent[];
  event_count: number;
  truncated: boolean;
  active_run: RunSummary | null;
};

type AccessMode = "read_only" | "workspace_write" | "full_access";

const ACCESS_MODE_LABELS: Record<AccessMode, string> = {
  read_only: "Read only",
  workspace_write: "Workspace access",
  full_access: "Full access",
};

type RunSummary = {
  run_id: string;
  thread_id: string;
  turn_id: string;
  status: string;
  access_mode: AccessMode;
  backend: "desktop_ipc" | "sdk";
  connection_state: string;
  owner_client_id?: string | null;
  started_at: number;
  completed_at: number | null;
  final_response: string | null;
  error: string | null;
  plan: Array<{ step: string; status: string }>;
  diff: string;
  last_seq: number;
};

type Project = {
  path: string;
  name: string;
  thread_count: number;
  updated_at: string | null;
  is_default: boolean;
};

const tokenSource = TokenSource.endpoint("/api/livekit/token");
const POLL_MS = 3000;
type ColorTheme = "system" | "light" | "dark";

const THEME_OPTIONS: Array<{ value: ColorTheme; label: string; icon: typeof Sun }> = [
  { value: "system", label: "System", icon: Monitor },
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
];

function ThemeMenu() {
  const [theme, setTheme] = useState<ColorTheme>(() => {
    if (typeof window === "undefined") return "system";
    const saved = window.localStorage.getItem("calldex.theme");
    return saved === "light" || saved === "dark" ? saved : "system";
  });

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = theme === "dark" || (theme === "system" && media.matches);
      document.documentElement.classList.toggle("dark", dark);
      document.documentElement.classList.toggle("light", !dark);
      document.documentElement.style.colorScheme = dark ? "dark" : "light";
    };
    apply();
    window.localStorage.setItem("calldex.theme", theme);
    if (theme === "system") media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);

  return (
    <div className="theme-control">
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Choose color theme" title="Color theme" />}><SunMoon /></DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="theme-menu">
          {THEME_OPTIONS.map(({ value, label, icon: Icon }) => (
            <DropdownMenuItem key={value} onClick={() => setTheme(value)}><Icon />{label}{theme === value && <Check className="theme-check" />}</DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

function relativeDate(value: string | null) {
  if (!value) return "unknown";
  const distance = Date.now() - new Date(value).getTime();
  if (distance < 60_000) return "now";
  if (distance < 3_600_000) return `${Math.floor(distance / 60_000)}m`;
  if (distance < 86_400_000) return `${Math.floor(distance / 3_600_000)}h`;
  return `${Math.floor(distance / 86_400_000)}d`;
}

function fullDate(value: string | null) {
  if (!value) return "Unknown";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function workedDuration(events: TimelineEvent[], now?: number) {
  const startedAt = events.map((event) => event.started_at ? new Date(event.started_at).getTime() : Number.NaN).filter(Number.isFinite);
  const completedAt = events.map((event) => event.completed_at ? new Date(event.completed_at).getTime() : Number.NaN).filter(Number.isFinite);
  if (startedAt.length === 0 || (now === undefined && completedAt.length === 0)) return null;
  const seconds = Math.max(0, Math.round(((now ?? Math.max(...completedAt)) - Math.min(...startedAt)) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

const WorkSummary = memo(function WorkSummary({ events, active }: { events: TimelineEvent[]; active: boolean }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  const duration = workedDuration(events, active ? now : undefined);
  const entries = useMemo(() => groupConsecutiveTools(
    events,
    (event) => isToolEventType(event.type),
    (event, index) => normalizePersistedTool(event, index),
  ), [events]);
  return (
    <details className={`work-summary${active ? " working" : ""}`} open={active}>
      <summary>{active ? "Working" : "Worked"}{duration ? ` for ${duration}` : ""}<ChevronRight size={15} /></summary>
      <div className="work-updates">
        {entries.map((entry) => entry.kind === "tools"
          ? <ToolActivityGroup group={entry.group} key={`tools-${entry.group.id}`} />
          : <MessageResponse className="message-response" key={entry.item.id}>{entry.item.summary}</MessageResponse>)}
      </div>
    </details>
  );
});

function eventCategory(type: string) {
  if (type === "userMessage" || type === "agentMessage") return "messages";
  if (isToolEventType(type)) return type === "fileChange" ? "changes" : "tools";
  if (type === "fileChange") return "changes";
  return "system";
}

function eventRole(type: string): UIMessage["role"] {
  if (type === "userMessage") return "user";
  if (type === "agentMessage") return "assistant";
  return "system";
}

function EventIcon({ type }: { type: string }) {
  if (type === "userMessage") return <UserRound size={13} />;
  if (type === "agentMessage") return <Bot size={13} />;
  if (type === "fileChange") return <Braces size={13} />;
  if (eventCategory(type) === "tools") return <Wrench size={13} />;
  return <Activity size={13} />;
}

async function api<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message || `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function postApi<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message || `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function patchApi<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

function LiveActivity({ run, events }: { run: RunSummary | null; events: RunEvent[] }) {
  if (run?.status !== "running") return null;
  const currentPlan = [...events].reverse().find((event) => event.type === "turn.plan.updated");
  const planPayload = currentPlan?.payload.plan;
  const plan = Array.isArray(planPayload) ? planPayload as Array<{ step?: string; status?: string }> : run?.plan || [];
  const reasoning = events
    .filter((event) => event.type.includes("reasoning") || event.type.includes("agentMessage.delta"))
    .map((event) => String(event.payload.delta || event.payload.text || ""))
    .join("");
  const tools = normalizeLiveTools(events);
  const userPrompts = events.filter((event) => event.type === "ui.user_message" || event.type === "run.started" || event.type === "run.steered");

  return (
    <section className="live-run" aria-label="Live Codex run">
      {userPrompts.map((event) => <Message from="user" className="live-user-message" key={`${event.type}-${event.seq}`}><MessageContent><p>{String(event.payload.prompt || "")}</p></MessageContent></Message>)}
      <div className="live-run-heading">
        <span className="live-dot" />
        Codex is {run.connection_state === "reconnecting" ? "reconnecting" : run.status || "working"}
        <Badge variant="outline" className="execution-backend">{run.backend === "desktop_ipc" ? "Desktop" : "SDK"}</Badge>
        {run.connection_state === "reconnecting" && <Button variant="ghost" size="sm" onClick={() => window.location.reload()}>Retry</Button>}
      </div>
      {plan.length > 0 && (
        <Plan defaultOpen isStreaming={run?.status === "running"} className="live-plan">
          <PlanHeader><PlanTitle>Plan</PlanTitle><PlanTrigger /></PlanHeader>
          <PlanContent><ol>{plan.map((step, index) => <li key={`${step.step}-${index}`} data-status={step.status}><span />{step.step}</li>)}</ol></PlanContent>
        </Plan>
      )}
      {reasoning && (
        <Reasoning isStreaming={run?.status === "running"} className="live-reasoning">
          <ReasoningTrigger />
          <ReasoningContent>{reasoning}</ReasoningContent>
        </Reasoning>
      )}
      {tools.length > 0 && <ToolActivityGroup group={{ id: `live-${run.run_id}`, activities: tools }} />}
    </section>
  );
}

const ThreadList = memo(function ThreadList({
  threads,
  selectedId,
  activeId,
  pendingId,
  onSelect,
}: {
  threads: ThreadSummary[];
  selectedId: string | null;
  activeId: string | null;
  pendingId: string | null;
  onSelect: (thread: ThreadSummary) => void;
}) {
  const groups = useMemo(() => {
    const result = new Map<string, ThreadSummary[]>();
    for (const thread of threads) {
      const current = result.get(thread.repository_path) || [];
      current.push(thread);
      result.set(thread.repository_path, current);
    }
    return Array.from(result.entries());
  }, [threads]);

  return (
    <nav className="thread-groups" aria-label="Recent Codex threads">
      {groups.length === 0 && <div className="sidebar-empty"><Search size={17} />No matching tasks</div>}
      {groups.map(([repository, items]) => (
        <Collapsible className="repo-group" key={repository} defaultOpen>
          <CollapsibleTrigger className="repo-heading">
            <ChevronDown className="repo-chevron" size={14} />
            <span title={repository}><FolderGit2 size={13} />{repository.split("/").filter(Boolean).pop() || repository}</span>
            <Badge variant="secondary">{items.length}</Badge>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="repo-threads">
              {items.map((thread) => (
                <button
                  className={`thread-row ${selectedId === thread.id ? "selected" : ""}`}
                  key={thread.id}
                  onClick={() => onSelect(thread)}
                  aria-current={selectedId === thread.id ? "page" : undefined}
                >
                  <span className="thread-title">
                    {thread.name}
                    {activeId === thread.id && <i className="voice-dot" title="Voice active" />}
                    {pendingId === thread.id && <i className="pending-dot" title="Voice selection pending" />}
                  </span>
                  <span className="thread-preview">{thread.preview || "No preview"}</span>
                  <span className="thread-meta"><b>{thread.status}</b><time title={fullDate(thread.updated_at)}>{relativeDate(thread.updated_at)}</time></span>
                </button>
              ))}
            </div>
          </CollapsibleContent>
        </Collapsible>
      ))}
    </nav>
  );
});

const Timeline = memo(function Timeline({ detail, loading, error, run, liveEvents, onRename, onFork, onArchive }: {
  detail: ThreadDetail | null;
  loading: boolean;
  error: string | null;
  run: RunSummary | null;
  liveEvents: RunEvent[];
  onRename: () => void;
  onFork: () => void;
  onArchive: () => void;
}) {
  const [filter, setFilter] = useState("messages");
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const [showInspector, setShowInspector] = useState(false);
  const filteredEvents = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return detail?.events.filter((event) => {
      if (filter === "messages" && eventCategory(event.type) !== "messages" && !isToolEventType(event.type)) return false;
      if (filter === "tools" && !isToolEventType(event.type)) return false;
      if (filter !== "all" && filter !== "messages" && filter !== "tools" && eventCategory(event.type) !== filter) return false;
      return !needle || `${event.title} ${event.summary} ${event.type}`.toLowerCase().includes(needle);
    }) || [];
  }, [detail, filter, query]);
  const downloadableMessages = useMemo<UIMessage[]>(() => filteredEvents.map((event) => ({
    id: event.id,
    role: eventRole(event.type),
    parts: [{ type: "text", text: event.summary }],
  })), [filteredEvents]);
  const timelineEntries = useMemo(() => {
    if (filter !== "messages") return groupConsecutiveTools(
      filteredEvents,
      (event) => isToolEventType(event.type),
      (event, index) => normalizePersistedTool(event, index),
    ).map((entry) => entry.kind === "tools"
      ? { kind: "tools" as const, group: entry.group }
      : { kind: "event" as const, event: entry.item });
    const entries: Array<
      { kind: "event"; event: TimelineEvent } |
      { kind: "work"; turnId: string; events: TimelineEvent[] }
    > = [];
    const workByTurn = new Map<string, { kind: "work"; turnId: string; events: TimelineEvent[] }>();
    for (const event of filteredEvents) {
      if ((event.type === "agentMessage" && event.phase === "commentary") || isToolEventType(event.type)) {
        let group = workByTurn.get(event.turn_id);
        if (!group) {
          group = { kind: "work", turnId: event.turn_id, events: [] };
          workByTurn.set(event.turn_id, group);
          entries.push(group);
        }
        group.events.push(event);
      } else {
        entries.push({ kind: "event", event });
      }
    }
    return entries;
  }, [filter, filteredEvents]);
  const latestTurnId = detail?.events.at(-1)?.turn_id;
  const latestTurnHasFinal = detail?.events.some((event) => event.turn_id === latestTurnId && event.type === "agentMessage" && event.phase === "final_answer") ?? false;

  const copy = async (value: string, label: string) => {
    await navigator.clipboard.writeText(value);
    setCopied(label);
    window.setTimeout(() => setCopied(null), 1200);
  };

  if (loading && !detail) return <div className="empty-state"><RefreshCw className="spin" /> Loading activity…</div>;
  if (error && !detail) return <div className="empty-state error"><CircleAlert />{error}</div>;
  if (!detail) return <div className="empty-state"><TerminalSquare />Select a thread to inspect its activity.</div>;

  return (
    <div className="timeline-shell">
      <header className="timeline-header">
        <div className="thread-heading-copy">
          <Folder size={16} />
          <div><h1>{detail.thread.name}</h1><p title={detail.thread.repository_path}>{detail.thread.repository_path}</p></div>
          <Button variant="ghost" size="icon-sm" aria-label="Toggle task details" aria-expanded={showInspector} onClick={() => setShowInspector((value) => !value)}><MoreHorizontal /></Button>
        </div>
        <div className="task-actions">
          {run?.status === "running" && <Badge className="run-badge"><span />{run.connection_state === "reconnecting" ? "Reconnecting" : "Running"}</Badge>}
          {run?.status === "running" && <Badge variant="outline" className="execution-backend">{run.backend === "desktop_ipc" ? "Desktop" : "SDK"}</Badge>}
          <Button variant="ghost" size="icon-sm" title="Rename task" onClick={onRename}><Pencil /></Button>
          <Button variant="ghost" size="icon-sm" title="Fork task" onClick={onFork}><GitFork /></Button>
          <Button variant="ghost" size="icon-sm" title="Archive task" onClick={onArchive}><Archive /></Button>
        </div>
      </header>
      {error && <Alert className="stale-banner"><CircleAlert size={15} /> Showing cached activity — {error}</Alert>}
      {showInspector && <div className="thread-inspector">
        {detail.truncated && <Alert className="truncated-note">Showing the latest 200 of {detail.event_count} events.</Alert>}
        <section className="thread-overview" aria-label="Thread overview"><span>{detail.event_count} events</span><i /> <span>{new Set(detail.events.map((event) => event.turn_id)).size} turns</span><i /> <span title={fullDate(detail.thread.updated_at)}>Updated {relativeDate(detail.thread.updated_at)}</span><Button variant="ghost" size="sm" onClick={() => void copy(detail.thread.id, "thread")}>{copied === "thread" ? <Check size={13} /> : <Copy size={13} />} Copy ID</Button></section>
        <div className="timeline-toolbar">
          <div className="event-filters" aria-label="Filter activity">
            {["all", "messages", "tools", "changes", "system"].map((value) => (
              <Button key={value} className={filter === value ? "active" : ""} variant={filter === value ? "secondary" : "ghost"} size="sm" onClick={() => setFilter(value)}>{value}</Button>
            ))}
          </div>
          <label className="event-search"><Search size={14} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search activity" aria-label="Search activity" /></label>
          <span className="event-result-count">{filteredEvents.length}/{detail.events.length}</span>
        </div>
      </div>}
      <Conversation className="thread-conversation" aria-label={`Conversation for ${detail.thread.name}`}>
        <ConversationContent className="timeline">
          {detail.events.length === 0 && <ConversationEmptyState icon={<TerminalSquare size={22} />} title="No activity yet" description="This task has no recorded conversation." />}
          {detail.events.length > 0 && filteredEvents.length === 0 && <ConversationEmptyState className="filtered-empty" icon={<Search size={18} />} title="No matching activity" description="Try another filter or search term." />}
          {timelineEntries.map((entry) => entry.kind === "work" ? (
            <WorkSummary
              active={(run?.status === "running" && run.turn_id === entry.turnId) || (entry.turnId === latestTurnId && !latestTurnHasFinal)}
              events={entry.events}
              key={`work-${entry.turnId}`}
            />
          ) : entry.kind === "tools" ? (
            <ToolActivityGroup group={entry.group} key={`tools-${entry.group.id}`} />
          ) : (() => {
            const event = entry.event;
            const activity = normalizePersistedTool(event);
            if (activity) return <ToolActivityGroup group={{ id: activity.id, activities: [activity] }} key={`tool-${activity.id}`} />;
            return (
            <Message from={eventRole(event.type)} className={`event event-${event.type}`} key={`${event.turn_id}-${event.id}`}>
              <MessageContent className="event-body">
              <div className="event-heading">
                <Badge variant="outline" className="event-badge"><EventIcon type={event.type} />{event.title}</Badge>
                <span>{event.phase || event.status}</span>
                <time>{event.timestamp ? new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}</time>
              </div>
              {eventCategory(event.type) === "messages" ? <MessageResponse className="message-response">{event.summary}</MessageResponse> : <p>{event.summary}</p>}
              </MessageContent>
              {eventCategory(event.type) === "messages" && (
                <MessageActions className="message-actions">
                  <MessageAction tooltip="Copy message" label="Copy message" onClick={() => void copy(event.summary, `message-${event.id}`)}>
                    {copied === `message-${event.id}` ? <Check /> : <Copy />}
                  </MessageAction>
                </MessageActions>
              )}
            </Message>
            );
          })())}
          <LiveActivity run={run} events={liveEvents} />
        </ConversationContent>
        {showInspector && filteredEvents.length > 0 && (
          <ConversationDownload
            messages={downloadableMessages}
            filename={`${detail.thread.name.replace(/[^a-z0-9_-]+/gi, "-").toLowerCase() || "codex-task"}.md`}
            className="conversation-download"
            aria-label="Download visible conversation"
            title="Download visible conversation"
          />
        )}
        <ConversationScrollButton className="conversation-scroll-button" aria-label="Scroll to latest message" title="Scroll to latest message" />
      </Conversation>
    </div>
  );
});

const VoiceComposer = memo(function VoiceComposer({ viewedThread, run, onConfirmed, onRun, onStop }: {
  viewedThread: ThreadSummary | null;
  run: RunSummary | null;
  onConfirmed: (id: string) => void;
  onRun: (run: RunSummary, prompt: string) => void;
  onStop: () => Promise<void>;
}) {
  const session = useSession(tokenSource, { agentName: "calldex" });
  return <SessionProvider session={session}><VoiceComposerInner session={session} viewedThread={viewedThread} run={run} onConfirmed={onConfirmed} onRun={onRun} onStop={onStop} /></SessionProvider>;
});

function Composer({ thread, run, onRun, onStop, voiceButton, voiceOverlay }: {
  thread: ThreadSummary | null;
  run: RunSummary | null;
  onRun: (run: RunSummary, prompt: string) => void;
  onStop: () => Promise<void>;
  voiceButton?: React.ReactNode;
  voiceOverlay?: React.ReactNode;
}) {
  const [prompt, setPrompt] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accessMode, setAccessMode] = useState<AccessMode>("workspace_write");
  const [confirmFullAccess, setConfirmFullAccess] = useState(false);

  const send = async (text = prompt) => {
    const value = text.trim();
    if (!thread || !value || sending) return;
    if (accessMode === "full_access" && !confirmFullAccess) {
      setConfirmFullAccess(true);
      return;
    }
    setSending(true);
    setError(null);
    try {
      const result = run?.status === "running"
        ? await postApi<{ run: RunSummary }>(`/api/runs/${encodeURIComponent(run.run_id)}/steer`, { prompt: value })
        : await postApi<{ run: RunSummary }>(`/api/threads/${encodeURIComponent(thread.id)}/turns`, {
            prompt: value,
            access_mode: accessMode,
            confirm_full_access: accessMode === "full_access",
          });
      setPrompt("");
      setConfirmFullAccess(false);
      onRun(result.run, value);
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : String(sendError));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="composer-dock">
      {voiceOverlay}
      <PromptInput className="composer" onSubmit={({ text }) => void send(text)}>
        <PromptInputTextarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder={thread ? run?.status === "running" ? "Steer the current run" : "Ask for follow-up changes" : "Select a task to continue"}
          aria-label="Message Codex"
          disabled={!thread || sending}
          maxLength={100_000}
        />
        <PromptInputFooter className="composer-footer">
          <PromptInputSelect value={accessMode} onValueChange={(value) => { setAccessMode(value as AccessMode); setConfirmFullAccess(false); }}>
            <PromptInputSelectTrigger className="access-mode"><ShieldCheck size={14} /><span>{ACCESS_MODE_LABELS[accessMode]}</span></PromptInputSelectTrigger>
            <PromptInputSelectContent>
              <PromptInputSelectItem value="read_only">Read only</PromptInputSelectItem>
              <PromptInputSelectItem value="workspace_write">Workspace access</PromptInputSelectItem>
              <PromptInputSelectItem value="full_access">Full access</PromptInputSelectItem>
            </PromptInputSelectContent>
          </PromptInputSelect>
          <span className="composer-hint">{run?.status === "running" ? "New messages steer this run" : "Enter to send · Shift Enter for newline"}</span>
          {voiceButton}
          <PromptInputSubmit
            className="send-message"
            status={run?.status === "running" ? "streaming" : sending ? "submitted" : "ready"}
            onStop={() => void onStop()}
            disabled={!thread || (!prompt.trim() && run?.status !== "running")}
          />
        </PromptInputFooter>
        {sending && <div className="composer-status"><Sparkles size={13} />Codex is working on this task…</div>}
        {confirmFullAccess && <div className="full-access-confirm"><CircleAlert size={14} /><span>Full access removes filesystem restrictions for this run.</span><Button size="sm" type="button" onClick={() => void send()}>Confirm and run</Button><Button size="sm" variant="ghost" type="button" onClick={() => setConfirmFullAccess(false)}>Cancel</Button></div>}
        {error && <div className="composer-error"><CircleAlert size={13} />{error}</div>}
      </PromptInput>
    </div>
  );
}

function VoiceComposerInner({ session, viewedThread, run, onConfirmed, onRun, onStop }: {
  session: ReturnType<typeof useSession>;
  viewedThread: ThreadSummary | null;
  run: RunSummary | null;
  onConfirmed: (id: string) => void;
  onRun: (run: RunSummary, prompt: string) => void;
  onStop: () => Promise<void>;
}) {
  const agent = useAgent(session);
  const { messages } = useSessionMessages(session);
  const [micEnabled, setMicEnabled] = useState(true);
  const [callError, setCallError] = useState<string | null>(null);
  const [connectedAt, setConnectedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const activeId = agent.attributes["calldex.activeThreadId"];
  const connected = session.connectionState === ConnectionState.Connected;
  const connecting = session.connectionState === ConnectionState.Connecting;
  const reconnecting = session.connectionState === ConnectionState.Reconnecting || session.connectionState === ConnectionState.SignalReconnecting;
  const viewedId = viewedThread?.id || null;
  const synchronized = connected && Boolean(viewedId) && activeId === viewedId;

  useEffect(() => {
    if (!connectedAt || !connected) return;
    const update = () => setElapsed(Math.floor((Date.now() - connectedAt) / 1000));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [connected, connectedAt]);

  useEffect(() => {
    if (activeId) onConfirmed(activeId);
  }, [activeId, onConfirmed]);

  useEffect(() => {
    if (!connected || !viewedId) return;
    void session.room.localParticipant.setAttributes({
      "calldex.requestedThreadId": viewedId,
      "calldex.requestedThreadNonce": crypto.randomUUID(),
    }).catch((error) => setCallError(String(error)));
  }, [agent.identity, connected, session.room, viewedId]);

  const connect = async () => {
    setCallError(null);
    try {
      await session.start({ tracks: { microphone: { enabled: true } } });
      setMicEnabled(true);
      setConnectedAt(Date.now());
    } catch (error) {
      setCallError(error instanceof Error ? error.message : String(error));
    }
  };

  const toggleMic = async () => {
    const next = !micEnabled;
    try {
      await session.room.localParticipant.setMicrophoneEnabled(next);
      setMicEnabled(next);
    } catch (error) {
      setCallError(error instanceof Error ? error.message : String(error));
    }
  };

  const end = async () => {
    await session.end();
    setConnectedAt(null);
    setElapsed(0);
  };

  const duration = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;
  const personaState: PersonaState = !connected
    ? "asleep"
    : agent.state === "listening" || agent.state === "thinking" || agent.state === "speaking"
      ? agent.state
      : "idle";
  const agentLabel = reconnecting
    ? "Reconnecting"
    : !connected
      ? "Ready when you are"
      : agent.state === "listening"
        ? "Listening"
        : agent.state === "thinking"
          ? "Thinking"
          : agent.state === "speaking"
            ? "Speaking"
            : "Connected";

  const latestMessage = messages.at(-1);
  const latestTranscript = latestMessage
    ? "message" in latestMessage
      ? latestMessage.message
      : "message" in (latestMessage as unknown as Record<string, unknown>)
        ? String((latestMessage as unknown as { message: string }).message)
        : ""
    : "";
  const latestSpeaker = latestMessage?.type === "agentTranscript" ? "Calldex" : "You";
  const voiceVisible = connecting || connected || reconnecting;

  const voiceButton = (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      className={`voice-launch${voiceVisible ? " active" : ""}`}
      onClick={() => { if (!voiceVisible) void connect(); }}
      disabled={connecting}
      aria-label={voiceVisible ? "Voice call active" : "Start voice call"}
      title={callError || (voiceVisible ? "Voice call active" : "Start voice call")}
    >
      {connecting ? <RefreshCw className="spin" /> : <Mic />}
    </Button>
  );

  const voiceOverlay = voiceVisible ? (
    <section className="voice-float" aria-label={`Voice call: ${agentLabel.toLowerCase()}`}>
      <div className="voice-float-topline">
        <span className={`voice-live-dot${reconnecting ? " reconnecting" : ""}`} />
        <span>{reconnecting ? "Reconnecting" : connected ? duration : "Connecting"}</span>
        {viewedThread && <span className="voice-task" title={viewedThread.name}>{synchronized ? <Check size={11} /> : <RefreshCw size={11} className="spin" />}{viewedThread.name}</span>}
      </div>
      <Persona state={personaState} variant="obsidian" className="voice-orb" />
      <strong className="voice-agent-state">{agentLabel}</strong>
      {latestTranscript && <div className={`voice-latest ${latestSpeaker === "You" ? "user" : "agent"}`}><span>{latestSpeaker}</span><p>{latestTranscript}</p></div>}
      <div className="voice-float-controls">
        <Button variant="ghost" size="icon" onClick={toggleMic} aria-label={micEnabled ? "Mute microphone" : "Unmute microphone"}>{micEnabled ? <Mic /> : <MicOff />}</Button>
        <Button variant="ghost" size="icon" className="voice-end" onClick={() => void end()} aria-label="End call"><PhoneOff /></Button>
      </div>
      <StartAudio label="Enable audio" className="voice-enable-audio" />
      {callError && <div className="voice-float-error"><CircleAlert size={13} />{callError}</div>}
    </section>
  ) : null;

  return (
    <>
      <RoomAudioRenderer />
      <Composer thread={viewedThread} run={run} onRun={onRun} onStop={onStop} voiceButton={voiceButton} voiceOverlay={voiceOverlay} />
    </>
  );
}

export default function Dashboard() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [threadQuery, setThreadQuery] = useState("");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [run, setRun] = useState<RunSummary | null>(null);
  const [liveEvents, setLiveEvents] = useState<RunEvent[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [dialog, setDialog] = useState<"new" | "rename" | "archive" | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [newPrompt, setNewPrompt] = useState("");
  const [newProject, setNewProject] = useState("");
  const [newAccess, setNewAccess] = useState<AccessMode>("workspace_write");
  const [fullAccessConfirmed, setFullAccessConfirmed] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const selectedRef = useRef(selectedId);
  const detailUpdatedAtRef = useRef<string | null>(null);
  const threadsSignatureRef = useRef("");
  const detailVersionRef = useRef("");
  const threadsRequestRef = useRef<Promise<ThreadSummary[] | null> | null>(null);
  const detailRequestRef = useRef<Map<string, Promise<void>>>(new Map());
  const searchRef = useRef<HTMLInputElement>(null);
  const activeRunId = run?.run_id;
  const activeRunStatus = run?.status;
  const activeRunThreadId = run?.thread_id;

  const loadThreads = useCallback((): Promise<ThreadSummary[] | null> => {
    if (threadsRequestRef.current) return threadsRequestRef.current;
    const request = (async () => {
      try {
        const result = await api<{ threads: ThreadSummary[] }>("/api/threads?limit=50");
        const signature = result.threads.map((thread) => `${thread.id}\u0000${thread.updated_at}\u0000${thread.status}\u0000${thread.name}\u0000${thread.preview}\u0000${thread.repository_path}`).join("\u0001");
        if (signature !== threadsSignatureRef.current) {
          threadsSignatureRef.current = signature;
          setThreads(result.threads);
        }
        setListError(null);
        setLastUpdated(new Date());
        const stored = localStorage.getItem("calldex.viewedThreadId");
        if (!selectedRef.current && result.threads.length) setSelectedId(result.threads.find((thread) => thread.id === stored)?.id || result.threads[0].id);
        return result.threads;
      } catch (error) {
        setListError(error instanceof Error ? error.message : String(error));
        return null;
      } finally {
        setLoading(false);
      }
    })();
    threadsRequestRef.current = request;
    void request.finally(() => { if (threadsRequestRef.current === request) threadsRequestRef.current = null; });
    return request;
  }, []);

  const loadDetail = useCallback((id: string): Promise<void> => {
    const current = detailRequestRef.current.get(id);
    if (current) return current;
    setDetailLoading(true);
    const request = (async () => {
      try {
        const result = await api<ThreadDetail>(`/api/threads/${encodeURIComponent(id)}`);
        if (selectedRef.current === id) {
          const last = result.events.at(-1);
          const version = `${id}\u0000${result.thread.updated_at}\u0000${result.event_count}\u0000${last?.id || ""}\u0000${last?.status || ""}`;
          detailUpdatedAtRef.current = result.thread.updated_at;
          if (version !== detailVersionRef.current) {
            detailVersionRef.current = version;
            setDetail(result);
          }
          setRun(result.active_run);
          setDetailError(null);
        }
      } catch (error) {
        if (selectedRef.current === id) setDetailError(error instanceof Error ? error.message : String(error));
      } finally {
        detailRequestRef.current.delete(id);
        if (selectedRef.current === id) setDetailLoading(false);
      }
    })();
    detailRequestRef.current.set(id, request);
    return request;
  }, []);

  useEffect(() => { selectedRef.current = selectedId; }, [selectedId]);
  useEffect(() => {
    const timer = window.setTimeout(() => void loadThreads(), 0);
    return () => window.clearTimeout(timer);
  }, [loadThreads]);
  useEffect(() => {
    if (!selectedId) return;
    localStorage.setItem("calldex.viewedThreadId", selectedId);
    const timer = window.setTimeout(() => void loadDetail(selectedId), 0);
    return () => window.clearTimeout(timer);
  }, [loadDetail, selectedId]);
  useEffect(() => {
    if (!activeRunId || activeRunStatus !== "running" || activeRunThreadId !== selectedId) return;
    let closed = false;
    const source = new EventSource(`/api/runs/${encodeURIComponent(activeRunId)}/events`);
    const onEvent = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent;
      setLiveEvents((current) => mergeRunEvent(current, event));
      if (event.type === "turn.diff.updated" && typeof event.payload.diff === "string") {
        setRun((current) => current ? { ...current, diff: event.payload.diff as string } : current);
      }
      if (event.type === "turn.plan.updated" && Array.isArray(event.payload.plan)) {
        setRun((current) => current ? { ...current, plan: event.payload.plan as RunSummary["plan"] } : current);
      }
      if (event.type === "run.finished") {
        const payload = event.payload;
        setRun((current) => current ? {
          ...current,
          status: String(payload.status || "completed"),
          final_response: typeof payload.final_response === "string" ? payload.final_response : current.final_response,
          error: typeof payload.error === "string" ? payload.error : null,
        } : current);
        source.close();
        if (!closed && selectedRef.current) {
          void Promise.all([loadDetail(selectedRef.current), loadThreads()]);
        }
      }
    };
    source.addEventListener("codex", onEvent as EventListener);
    source.onerror = () => undefined;
    return () => { closed = true; source.close(); };
  }, [activeRunId, activeRunStatus, activeRunThreadId, loadDetail, loadThreads, selectedId]);
  useEffect(() => {
    const tick = async () => {
      if (document.hidden) return;
      const result = await loadThreads();
      const id = selectedRef.current;
      const selected = result?.find((thread) => thread.id === id);
      if (id && run?.status !== "running" && selected?.updated_at !== detailUpdatedAtRef.current) await loadDetail(id);
    };
    const timer = window.setInterval(tick, POLL_MS);
    const onVisibilityChange = () => { if (!document.hidden) void tick(); };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisibilityChange); };
  }, [loadDetail, loadThreads, run?.status]);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    document.addEventListener("keydown", focusSearch);
    return () => document.removeEventListener("keydown", focusSearch);
  }, []);

  const choose = useCallback((thread: ThreadSummary) => {
    selectedRef.current = thread.id;
    detailUpdatedAtRef.current = null;
    detailVersionRef.current = "";
    setDetail(null);
    setRun(null);
    setLiveEvents([]);
    setDetailError(null);
    setSelectedId(thread.id);
    setPendingId(thread.id === activeId ? null : thread.id);
    setDrawerOpen(false);
  }, [activeId]);
  const confirm = useCallback((id: string) => {
    selectedRef.current = id;
    detailUpdatedAtRef.current = null;
    detailVersionRef.current = "";
    setActiveId(id);
    setPendingId(null);
    setSelectedId(id);
  }, []);
  const navigate = (event: React.KeyboardEvent) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const index = threads.findIndex((thread) => thread.id === selectedId);
    const next = event.key === "ArrowDown" ? Math.min(threads.length - 1, index + 1) : Math.max(0, index - 1);
    if (threads[next]) { event.preventDefault(); choose(threads[next]); }
  };
  const filteredThreads = useMemo(() => {
    const needle = threadQuery.trim().toLowerCase();
    if (!needle) return threads;
    return threads.filter((thread) => `${thread.name} ${thread.preview} ${thread.repository_path}`.toLowerCase().includes(needle));
  }, [threadQuery, threads]);
  const selectedThread = threads.find((thread) => thread.id === selectedId) || detail?.thread || null;
  const refresh = () => {
    void loadThreads();
    if (selectedRef.current) void loadDetail(selectedRef.current);
  };

  const openNewTask = async () => {
    setDialogError(null);
    setFullAccessConfirmed(false);
    setDialog("new");
    try {
      const result = await api<{ projects: Project[] }>("/api/projects");
      setProjects(result.projects);
      setNewProject(result.projects.find((project) => project.is_default)?.path || result.projects[0]?.path || "");
    } catch (error) {
      setDialogError(error instanceof Error ? error.message : String(error));
    }
  };

  const createTask = async () => {
    if (!newPrompt.trim() || !newProject) return;
    setDialogBusy(true);
    setDialogError(null);
    try {
      const result = await postApi<{ thread: ThreadSummary; run: RunSummary }>("/api/threads", {
        prompt: newPrompt,
        repository_path: newProject,
        access_mode: newAccess,
        confirm_full_access: newAccess === "full_access" && fullAccessConfirmed,
      });
      setThreads((current) => [result.thread, ...current.filter((thread) => thread.id !== result.thread.id)]);
      selectedRef.current = result.thread.id;
      setSelectedId(result.thread.id);
      setDetail(null);
      setRun(result.run);
      setLiveEvents([]);
      setNewPrompt("");
      setDialog(null);
    } catch (error) {
      setDialogError(error instanceof Error ? error.message : String(error));
    } finally {
      setDialogBusy(false);
    }
  };

  const renameTask = async () => {
    if (!selectedThread || !renameValue.trim()) return;
    setDialogBusy(true);
    try {
      const result = await patchApi<{ thread: ThreadSummary }>(`/api/threads/${encodeURIComponent(selectedThread.id)}`, { name: renameValue });
      setThreads((current) => current.map((thread) => thread.id === result.thread.id ? { ...thread, name: result.thread.name } : thread));
      setDetail((current) => current ? { ...current, thread: { ...current.thread, name: result.thread.name } } : current);
      setDialog(null);
    } catch (error) {
      setDialogError(error instanceof Error ? error.message : String(error));
    } finally {
      setDialogBusy(false);
    }
  };

  const forkTask = async () => {
    if (!selectedThread) return;
    try {
      const result = await postApi<{ thread: ThreadSummary }>(`/api/threads/${encodeURIComponent(selectedThread.id)}/fork`, {});
      setThreads((current) => [result.thread, ...current]);
      choose(result.thread);
    } catch (error) {
      setDetailError(error instanceof Error ? error.message : String(error));
    }
  };

  const archiveTask = async () => {
    if (!selectedThread) return;
    setDialogBusy(true);
    try {
      await postApi<never>(`/api/threads/${encodeURIComponent(selectedThread.id)}/archive`, { confirm: true });
      const remaining = threads.filter((thread) => thread.id !== selectedThread.id);
      setThreads(remaining);
      setDialog(null);
      if (remaining[0]) choose(remaining[0]);
      else { setSelectedId(null); setDetail(null); }
    } catch (error) {
      setDialogError(error instanceof Error ? error.message : String(error));
    } finally {
      setDialogBusy(false);
    }
  };

  return (
    <main className="dashboard" onKeyDown={navigate}>
      <ThemeMenu />
      <aside className={`sidebar ${drawerOpen ? "drawer-open" : ""}`}>
        <header className="brand"><div><strong>Codex</strong><span><i /> Calldex voice</span></div><Button variant="ghost" size="icon-sm" onClick={() => searchRef.current?.focus()} aria-label="Search tasks"><Search /></Button><button className="close-drawer" onClick={() => setDrawerOpen(false)} aria-label="Close threads"><X /></button></header>
        <div className="sidebar-primary"><Button onClick={() => void openNewTask()}><Plus size={15} />New task</Button><Button variant="ghost" size="icon-sm" onClick={refresh} aria-label="Refresh tasks"><RefreshCw size={14} /></Button></div>
        <label className="thread-search"><Search size={14} /><Input ref={searchRef} value={threadQuery} onChange={(event) => setThreadQuery(event.target.value)} placeholder="Search tasks and repositories" aria-label="Search tasks" />{threadQuery ? <Button variant="ghost" size="icon-sm" onClick={() => setThreadQuery("")} aria-label="Clear search"><X /></Button> : <kbd>⌘K</kbd>}</label>
        <div className="sidebar-heading"><span>Recent tasks</span><small>{filteredThreads.length}/{threads.length}</small></div>
        {loading && <div className="sidebar-note"><Skeleton className="h-4 w-4 rounded-full" /><Skeleton className="h-4 w-32" /></div>}
        {listError && <Alert className="sidebar-note error"><CircleAlert size={15} />{listError}</Alert>}
        {!loading && !listError && threads.length === 0 && <div className="sidebar-note">No recent threads found.</div>}
        <ScrollArea className="min-h-0 flex-1"><ThreadList threads={filteredThreads} selectedId={selectedId} activeId={activeId} pendingId={pendingId} onSelect={choose} /></ScrollArea>
        <footer className="sidebar-footer"><Database size={13} /><span>{listError ? "Refresh failed" : lastUpdated ? `Updated ${relativeDate(lastUpdated.toISOString())}` : "Loading local state"}</span><i className={listError ? "error-dot" : ""} /></footer>
      </aside>
      {drawerOpen && <button className="drawer-scrim" onClick={() => setDrawerOpen(false)} aria-label="Close thread drawer" />}
      <section className="workspace">
        <button className="mobile-menu" onClick={() => setDrawerOpen(true)}><Menu size={18} />Threads</button>
        <div className="workspace-scroll"><Timeline
          key={selectedId || "empty"}
          detail={detail}
          loading={detailLoading || loading}
          error={detailError}
          run={run}
          liveEvents={liveEvents}
          onRename={() => { setRenameValue(selectedThread?.name || ""); setDialogError(null); setDialog("rename"); }}
          onFork={() => void forkTask()}
          onArchive={() => { setDialogError(null); setDialog("archive"); }}
        /></div>
        <VoiceComposer
          viewedThread={selectedThread}
          run={run}
          onConfirmed={confirm}
          onRun={(nextRun, prompt) => {
            setRun(nextRun);
            setLiveEvents((current) => [optimisticUserEvent(nextRun.run_id, nextRun.thread_id, nextRun.turn_id, prompt), ...current.filter((event) => event.run_id === nextRun.run_id)].slice(-500));
          }}
          onStop={async () => {
            if (!run) return;
            const result = await postApi<{ run: RunSummary }>(`/api/runs/${encodeURIComponent(run.run_id)}/interrupt`, {});
            setRun(result.run);
          }}
        />
      </section>
      <Dialog open={dialog === "new"} onOpenChange={(open) => { if (!open) setDialog(null); }}>
        <DialogContent className="task-dialog">
          <DialogHeader><DialogTitle>New Codex task</DialogTitle><DialogDescription>Choose a known repository and describe the work.</DialogDescription></DialogHeader>
          <label>Repository<select value={newProject} onChange={(event) => setNewProject(event.target.value)}>{projects.map((project) => <option value={project.path} key={project.path}>{project.name} · {project.path}</option>)}</select></label>
          <label>Task<textarea value={newPrompt} onChange={(event) => setNewPrompt(event.target.value)} placeholder="What should Codex do?" rows={5} /></label>
          <label>Access<select value={newAccess} onChange={(event) => { setNewAccess(event.target.value as AccessMode); setFullAccessConfirmed(false); }}><option value="read_only">Read only</option><option value="workspace_write">Workspace access</option><option value="full_access">Full access</option></select></label>
          {newAccess === "full_access" && !fullAccessConfirmed && <Confirmation approval={{ id: "full-access" }} state="approval-requested" className="dialog-warning"><ConfirmationRequest><ConfirmationTitle>Full access can read and modify files outside this repository. Confirm before starting this task.</ConfirmationTitle><ConfirmationActions><ConfirmationAction variant="ghost" onClick={() => setNewAccess("workspace_write")}>Use workspace access</ConfirmationAction><ConfirmationAction onClick={() => setFullAccessConfirmed(true)}>Confirm full access</ConfirmationAction></ConfirmationActions></ConfirmationRequest></Confirmation>}
          {newAccess === "full_access" && fullAccessConfirmed && <Alert className="dialog-warning"><ShieldCheck />Full access confirmed for this run.</Alert>}
          {dialogError && <Alert className="error"><CircleAlert />{dialogError}</Alert>}
          <DialogFooter><Button variant="ghost" onClick={() => setDialog(null)}>Cancel</Button><Button disabled={dialogBusy || !newPrompt.trim() || !newProject || (newAccess === "full_access" && !fullAccessConfirmed)} onClick={() => void createTask()}>{dialogBusy ? <RefreshCw className="spin" /> : <Plus />}Create task</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={dialog === "rename"} onOpenChange={(open) => { if (!open) setDialog(null); }}>
        <DialogContent className="task-dialog"><DialogHeader><DialogTitle>Rename task</DialogTitle></DialogHeader><Input value={renameValue} onChange={(event) => setRenameValue(event.target.value)} autoFocus />{dialogError && <Alert className="error">{dialogError}</Alert>}<DialogFooter><Button variant="ghost" onClick={() => setDialog(null)}>Cancel</Button><Button disabled={dialogBusy || !renameValue.trim()} onClick={() => void renameTask()}>Rename</Button></DialogFooter></DialogContent>
      </Dialog>
      <Dialog open={dialog === "archive"} onOpenChange={(open) => { if (!open) setDialog(null); }}>
        <DialogContent className="task-dialog"><DialogHeader><DialogTitle>Archive this task?</DialogTitle><DialogDescription>It will disappear from Calldex’s recent tasks but remains in Codex’s archive.</DialogDescription></DialogHeader><Confirmation approval={{ id: "archive" }} state="approval-requested"><ConfirmationRequest><ConfirmationTitle>Archiving removes this task from the recent task list.</ConfirmationTitle><ConfirmationActions><ConfirmationAction variant="ghost" onClick={() => setDialog(null)}>Cancel</ConfirmationAction><ConfirmationAction variant="destructive" disabled={dialogBusy} onClick={() => void archiveTask()}><Archive />Archive</ConfirmationAction></ConfirmationActions></ConfirmationRequest></Confirmation>{dialogError && <Alert className="error">{dialogError}</Alert>}</DialogContent>
      </Dialog>
    </main>
  );
}
