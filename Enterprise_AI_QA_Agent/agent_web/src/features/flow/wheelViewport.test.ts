import { describe, expect, it } from "vitest";

import { wheelViewportTransform } from "./wheelViewport";

const viewport = { x: 100, y: 200, zoom: 1 };
const rect = { left: 10, top: 20 };

describe("wheelViewportTransform", () => {
  it("moves vertically for an unmodified wheel", () => {
    expect(wheelViewportTransform(viewport, {
      deltaX: 0, deltaY: 40, deltaMode: 0, shiftKey: false, ctrlKey: false,
      clientX: 0, clientY: 0,
    }, rect, 0.35, 1.6)).toEqual({ x: 100, y: 160, zoom: 1 });
  });

  it("moves horizontally for shift plus wheel", () => {
    expect(wheelViewportTransform(viewport, {
      deltaX: 0, deltaY: 40, deltaMode: 0, shiftKey: true, ctrlKey: false,
      clientX: 0, clientY: 0,
    }, rect, 0.35, 1.6)).toEqual({ x: 60, y: 200, zoom: 1 });
  });

  it("zooms around the cursor for ctrl plus wheel", () => {
    const next = wheelViewportTransform(viewport, {
      deltaX: 0, deltaY: -100, deltaMode: 0, shiftKey: false, ctrlKey: true,
      clientX: 110, clientY: 220,
    }, rect, 0.35, 1.6);
    expect(next.zoom).toBeGreaterThan(1);
    expect((110 - rect.left - next.x) / next.zoom).toBeCloseTo((110 - rect.left - viewport.x) / viewport.zoom);
    expect((220 - rect.top - next.y) / next.zoom).toBeCloseTo((220 - rect.top - viewport.y) / viewport.zoom);
  });
});
