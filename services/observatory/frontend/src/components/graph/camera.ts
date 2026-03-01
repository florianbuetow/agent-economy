import { WORLD_SIZE } from "./types";

export interface Camera {
  x: number; // world center x
  y: number; // world center y
  zoom: number;
}

export function createCamera(): Camera {
  return {
    x: WORLD_SIZE / 2,
    y: WORLD_SIZE / 2,
    zoom: 1, // will be computed to fit viewport
  };
}

/** Compute zoom level to fit entire world in the given viewport */
export function fitToViewport(camera: Camera, viewportW: number, viewportH: number): void {
  const scaleX = viewportW / WORLD_SIZE;
  const scaleY = viewportH / WORLD_SIZE;
  camera.zoom = Math.min(scaleX, scaleY) * 0.92; // 8% padding
  camera.x = WORLD_SIZE / 2;
  camera.y = WORLD_SIZE / 2;
}

/** Convert world coordinates to screen coordinates */
export function worldToScreen(
  wx: number,
  wy: number,
  camera: Camera,
  canvasW: number,
  canvasH: number,
): { sx: number; sy: number } {
  return {
    sx: (wx - camera.x) * camera.zoom + canvasW / 2,
    sy: (wy - camera.y) * camera.zoom + canvasH / 2,
  };
}

/** Check if a circle at world position is visible on screen */
export function isVisible(
  wx: number,
  wy: number,
  r: number,
  camera: Camera,
  canvasW: number,
  canvasH: number,
): boolean {
  const { sx, sy } = worldToScreen(wx, wy, camera, canvasW, canvasH);
  const screenR = r * camera.zoom;
  const margin = 60;
  return (
    sx + screenR + margin > 0 &&
    sx - screenR - margin < canvasW &&
    sy + screenR + margin > 0 &&
    sy - screenR - margin < canvasH
  );
}
