/* GENERATED from design/tokens.css. Run node scripts/export-liquid-glass.cjs.
 * React/DialKit integration reference; not loaded by the native design specimen.
 */
"use client";
import { useDialKit } from "dialkit";

export function useLiquidGlassControls() {
  return useDialKit("Controls", {
    shape: { type: "select", options: ["circle", "pill"], default: "circle" },
    scale: [50, 0, 100],
    aberration: [20, 0, 30],
    bevelDepth: [60, 0, 100],
    frost: [4.8, 0, 10, 0.1],
    tint: [0.05, 0, 1, 0.01],
  });
}
