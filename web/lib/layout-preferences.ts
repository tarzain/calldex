export type VoiceLayoutMode = "floating" | "docked";

export const DESKTOP_BREAKPOINT = 820;
export const DEFAULT_VOICE_PANE_WIDTH = 360;
export const MIN_VOICE_PANE_WIDTH = 280;
export const MAX_VOICE_PANE_WIDTH = 520;
export const DEFAULT_VOICE_SHEET_HEIGHT = 300;
export const MIN_VOICE_SHEET_HEIGHT = 220;

export function clampVoicePaneWidth(value: number, viewportWidth: number) {
  const maximum = Math.max(MIN_VOICE_PANE_WIDTH, Math.min(MAX_VOICE_PANE_WIDTH, viewportWidth * 0.45));
  return Math.round(Math.min(maximum, Math.max(MIN_VOICE_PANE_WIDTH, value)));
}

export function clampVoiceSheetHeight(value: number, viewportHeight: number) {
  const maximum = Math.max(MIN_VOICE_SHEET_HEIGHT, viewportHeight * 0.55);
  return Math.round(Math.min(maximum, Math.max(MIN_VOICE_SHEET_HEIGHT, value)));
}

export function readStoredNumber(value: string | null, fallback: number) {
  if (value === null || value.trim() === "") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function readVoiceLayout(value: string | null): VoiceLayoutMode {
  return value === "docked" ? "docked" : "floating";
}

export function resizeVoiceDimension(startSize: number, delta: number, compact: boolean, viewportSize: number) {
  return compact
    ? clampVoiceSheetHeight(startSize + delta, viewportSize)
    : clampVoicePaneWidth(startSize + delta, viewportSize);
}

export function keyboardResizeValue({ key, current, minimum, maximum, compact, shiftKey = false }: {
  key: string;
  current: number;
  minimum: number;
  maximum: number;
  compact: boolean;
  shiftKey?: boolean;
}) {
  const step = shiftKey ? 32 : 16;
  if (key === "Home") return minimum;
  if (key === "End") return maximum;
  if ((!compact && key === "ArrowLeft") || (compact && key === "ArrowUp")) return Math.min(maximum, current + step);
  if ((!compact && key === "ArrowRight") || (compact && key === "ArrowDown")) return Math.max(minimum, current - step);
  return null;
}
