import type { ViewportTransform } from "@vue-flow/core";

export interface WheelViewportRect {
  left: number;
  top: number;
}

const WHEEL_ZOOM_FACTOR = 0.002;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

export function wheelViewportTransform(
  viewport: ViewportTransform,
  event: Pick<WheelEvent, "deltaX" | "deltaY" | "deltaMode" | "shiftKey" | "ctrlKey" | "clientX" | "clientY">,
  rect: WheelViewportRect,
  minimumZoom: number,
  maximumZoom: number,
): ViewportTransform {
  const multiplier = event.deltaMode === 1 ? 20 : 1;
  const deltaX = event.deltaX * multiplier;
  const deltaY = event.deltaY * multiplier;

  if (event.ctrlKey) {
    const nextZoom = clamp(
      viewport.zoom * Math.exp(-deltaY * WHEEL_ZOOM_FACTOR),
      minimumZoom,
      maximumZoom,
    );
    const pointX = event.clientX - rect.left;
    const pointY = event.clientY - rect.top;
    const ratio = nextZoom / viewport.zoom;
    return {
      x: pointX - (pointX - viewport.x) * ratio,
      y: pointY - (pointY - viewport.y) * ratio,
      zoom: nextZoom,
    };
  }

  if (event.shiftKey) {
    const horizontalDelta = deltaX || deltaY;
    return { ...viewport, x: viewport.x - horizontalDelta };
  }

  return { ...viewport, y: viewport.y - deltaY };
}
