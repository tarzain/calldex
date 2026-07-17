import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the local dashboard shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>Calldex/);
  assert.match(html, />Codex</);
  assert.match(html, /Recent tasks/);
  assert.match(html, /New task/);
  assert.match(html, /Search tasks and repositories/);
  assert.match(html, />Voice</);
  assert.match(html, /Transcript/);
  assert.match(html, /Select a task to continue/);
});

test("implements streaming, safe rendering, lifecycle controls, and two-way voice selection", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const message = await readFile(new URL("../components/ai-elements/message.tsx", import.meta.url), "utf8");
  const persona = await readFile(new URL("../components/ai-elements/persona.tsx", import.meta.url), "utf8");
  const messageRoute = await readFile(new URL("../app/api/threads/[threadId]/messages/route.ts", import.meta.url), "utf8");
  assert.match(page, /const POLL_MS = 3000/);
  assert.match(page, /function ThemeMenu/);
  assert.match(page, /<main className="dashboard"[^>]*>\s*<ThemeMenu \/>/);
  assert.match(page, /calldex\.theme/);
  assert.match(page, /prefers-color-scheme: dark/);
  assert.match(layout, /themeScript/);
  assert.doesNotMatch(layout, /className=\{cn\("dark/);
  assert.match(page, /document\.hidden/);
  assert.match(page, /threadsRequestRef\.current/);
  assert.match(page, /selected\?\.updated_at !== detailUpdatedAtRef\.current/);
  assert.doesNotMatch(page, /void loadThreads\(\); if \(selectedRef\.current\) void loadDetail/);
  assert.match(page, /const Timeline = memo/);
  assert.match(page, /const CallPanel = memo/);
  assert.match(page, /calldex\.viewedThreadId/);
  assert.match(page, /calldex\.requestedThreadId/);
  assert.match(page, /calldex\.activeThreadId/);
  assert.match(page, /JSON\.stringify\(event\.details/);
  assert.doesNotMatch(page, /dangerouslySetInnerHTML/);
  assert.match(page, /ArrowDown/);
  assert.match(page, /setMicrophoneEnabled/);
  assert.match(page, /RoomAudioRenderer/);
  assert.match(page, /@\/components\/ui\/button/);
  assert.match(page, /@\/components\/ui\/collapsible/);
  assert.match(page, /@\/components\/ui\/input/);
  assert.match(page, /@\/components\/ai-elements\/prompt-input/);
  assert.match(page, /@\/components\/ai-elements\/reasoning/);
  assert.match(page, /@\/components\/ai-elements\/plan/);
  assert.match(page, /@\/components\/ai-elements\/tool/);
  assert.match(page, /@\/components\/ai-elements\/conversation/);
  assert.match(page, /@\/components\/ai-elements\/message/);
  assert.match(page, /@\/components\/ai-elements\/persona/);
  assert.match(page, /<Persona state=\{personaState\} variant="obsidian"/);
  assert.doesNotMatch(page, /BarVisualizer/);
  assert.match(page, /<Conversation className="thread-conversation"/);
  assert.match(page, /<ConversationContent className="timeline"/);
  assert.match(page, /<ConversationScrollButton/);
  assert.match(page, /<ConversationDownload/);
  assert.match(page, /<Message from=\{eventRole\(event\.type\)\}/);
  assert.match(page, /<MessageAction tooltip="Copy message"/);
  assert.match(page, /<MessageResponse className="message-response"/);
  assert.match(page, /className=\{`work-summary\$\{active \? " working" : ""\}`\}/);
  assert.match(page, /active \? "Working" : "Worked"/);
  assert.match(page, /open=\{active\}/);
  assert.match(page, /latestTurnHasFinal/);
  assert.match(page, /event\.phase === "commentary"/);
  assert.match(page, /Search activity/);
  assert.match(page, /className="voice-context"/);
  assert.match(page, /new EventSource/);
  assert.match(page, /\/turns/);
  assert.match(page, /\/steer/);
  assert.match(page, /\/interrupt/);
  assert.match(page, /New task/);
  assert.match(page, /Rename task/);
  assert.match(page, /Archive task/);
  assert.match(page, /Steer the current run/);
  assert.match(page, /ACCESS_MODE_LABELS\[accessMode\]/);
  assert.match(page, /className="send-message"/);
  assert.match(messageRoute, /export async function POST/);
  assert.match(messageRoute, /\/api\/threads\/\$\{encodeURIComponent\(threadId\)\}\/messages/);
  assert.match(message, /<TooltipTrigger render=\{button\} \/>/);
  assert.doesNotMatch(message, /<TooltipTrigger>\{button\}<\/TooltipTrigger>/);
  assert.match(message, /data-slot="message-content"/);
  assert.doesNotMatch(message, /@streamdown\/(?:cjk|code|math|mermaid)/);
  assert.match(persona, /@rive-app\/react-webgl2/);
  assert.doesNotMatch(persona, /react-canvas/);
});
