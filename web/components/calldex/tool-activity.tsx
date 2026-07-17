"use client";

import { useState } from "react";
import { BookOpen, ChevronRight, CircleAlert, Image as ImageIcon, Pencil, Search, TerminalSquare, Users, Wrench } from "lucide-react";
import { CodeBlock } from "@/components/ai-elements/code-block";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { ToolActivity, ToolActivityGroup as ToolActivityGroupValue } from "@/lib/tool-activity";
import { summarizeToolActivities } from "@/lib/tool-activity";

const activityIcon = (activity: ToolActivity) => {
  if (activity.kind === "command") return <TerminalSquare size={16} />;
  if (activity.kind === "fileChange") return <Pencil size={16} />;
  if (activity.kind === "search") return <Search size={16} />;
  if (activity.kind === "collaboration") return <Users size={16} />;
  if (activity.kind === "imageView" || activity.kind === "imageGeneration") return <ImageIcon size={16} />;
  if (/read|fetch|get|open/i.test(`${activity.tool || ""} ${activity.title}`)) return <BookOpen size={16} />;
  return <Wrench size={16} />;
};

const printable = (value: unknown) => {
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2) ?? String(value); } catch { return String(value); }
};

const valueRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

const rowLabel = (activity: ToolActivity) => {
  const active = activity.status === "inProgress" || activity.status === "running";
  if (activity.kind === "command") return `${active ? "Running" : "Ran"} ${activity.command || activity.title}`;
  if (activity.kind === "fileChange" && activity.paths?.length) return `${active ? "Editing" : "Edited"} ${activity.paths.join(", ")}`;
  return activity.title;
};

function ToolActivityRow({ activity }: { activity: ToolActivity }) {
  const [open, setOpen] = useState(false);
  const details = Boolean(activity.command || activity.cwd || activity.input != null || activity.output || activity.error || activity.changes?.length || activity.progress?.length);
  return (
    <Collapsible className={`tool-activity-row status-${activity.status}`} open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="tool-activity-row-trigger" disabled={!details} aria-label={`${open ? "Collapse" : "Expand"} ${activity.title}`}>
        {activityIcon(activity)}
        <span>{rowLabel(activity)}</span>
        {activity.durationMs != null && <small>{(activity.durationMs / 1000).toFixed(1)}s</small>}
        {activity.exitCode != null && <small>exit {activity.exitCode}</small>}
        <small className="tool-status">{activity.status === "inProgress" ? "running" : activity.status}</small>
        {activity.status === "inProgress" && <i className="tool-running" />}
        {details && <ChevronRight className="tool-row-chevron" size={14} />}
      </CollapsibleTrigger>
      <CollapsibleContent className="tool-activity-detail">
        {activity.cwd && <div className="tool-meta"><span>Working directory</span><code>{activity.cwd}</code></div>}
        {activity.input != null && <div><span className="tool-detail-label">Arguments</span><CodeBlock code={printable(activity.input)} language="json" /></div>}
        {activity.changes?.map((change, index) => {
          const value = valueRecord(change);
          const path = String(value.path || value.file || value.filePath || `Change ${index + 1}`);
          const diff = value.diff ?? value.patch ?? value.content;
          return <div key={`${activity.id}-change-${index}`}><span className="tool-detail-label">{path} · {String(value.kind || value.operation || "updated")}</span>{diff != null && <CodeBlock code={printable(diff)} language="diff" />}</div>;
        })}
        {activity.progress?.map((message, index) => <p className="tool-progress" key={`${activity.id}-progress-${index}`}>{message}</p>)}
        {activity.output && <div><span className="tool-detail-label">Output</span><CodeBlock code={activity.output} language={activity.kind === "command" ? "shell" : "text"} /></div>}
        {activity.error && <div className="tool-error"><CircleAlert size={14} />{activity.error}</div>}
      </CollapsibleContent>
    </Collapsible>
  );
}

export function ToolActivityGroup({ group }: { group: ToolActivityGroupValue }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible className="tool-activity-group" open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="tool-activity-summary" aria-label={`${open ? "Collapse" : "Expand"} tool activity`}>
        <Wrench size={16} />
        <span>{summarizeToolActivities(group.activities)}</span>
        <ChevronRight className="tool-group-chevron" size={15} />
      </CollapsibleTrigger>
      <CollapsibleContent className="tool-activity-rows">
        {group.activities.map((activity) => <ToolActivityRow activity={activity} key={activity.id} />)}
      </CollapsibleContent>
    </Collapsible>
  );
}
