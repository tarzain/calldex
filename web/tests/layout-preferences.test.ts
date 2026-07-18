import { describe, expect, it } from "vitest";
import {
  clampVoicePaneWidth,
  clampVoiceSheetHeight,
  keyboardResizeValue,
  readStoredNumber,
  readVoiceLayout,
  resizeVoiceDimension,
} from "@/lib/layout-preferences";

describe("layout preferences", () => {
  it("clamps desktop pane widths to fixed and viewport bounds", () => {
    expect(clampVoicePaneWidth(100, 1440)).toBe(280);
    expect(clampVoicePaneWidth(420, 1440)).toBe(420);
    expect(clampVoicePaneWidth(900, 1440)).toBe(520);
    expect(clampVoicePaneWidth(500, 900)).toBe(405);
  });

  it("clamps bottom sheet heights to the available viewport", () => {
    expect(clampVoiceSheetHeight(100, 800)).toBe(220);
    expect(clampVoiceSheetHeight(320, 800)).toBe(320);
    expect(clampVoiceSheetHeight(900, 800)).toBe(440);
  });

  it("falls back safely for invalid stored preferences", () => {
    expect(readStoredNumber(null, 360)).toBe(360);
    expect(readStoredNumber("not-a-number", 300)).toBe(300);
    expect(readVoiceLayout("docked")).toBe("docked");
    expect(readVoiceLayout("unexpected")).toBe("floating");
  });

  it("resizes with pointer deltas and keyboard controls", () => {
    expect(resizeVoiceDimension(360, 40, false, 1440)).toBe(400);
    expect(resizeVoiceDimension(300, -200, true, 800)).toBe(220);
    expect(keyboardResizeValue({ key: "ArrowLeft", current: 360, minimum: 280, maximum: 520, compact: false })).toBe(376);
    expect(keyboardResizeValue({ key: "ArrowDown", current: 300, minimum: 220, maximum: 440, compact: true, shiftKey: true })).toBe(268);
    expect(keyboardResizeValue({ key: "Home", current: 360, minimum: 280, maximum: 520, compact: false })).toBe(280);
    expect(keyboardResizeValue({ key: "End", current: 300, minimum: 220, maximum: 440, compact: true })).toBe(440);
    expect(keyboardResizeValue({ key: "Enter", current: 300, minimum: 220, maximum: 440, compact: true })).toBeNull();
  });
});
