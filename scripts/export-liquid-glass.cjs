/* Build-time export only; Node 24 type stripping needs no npm dependencies. */
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { stripTypeScriptTypes } = require("node:module");
const root = path.resolve(__dirname, "..");
const css = fs.readFileSync(path.join(root, "design/tokens.css"), "utf8");
const value = token => {
  const match = css.match(new RegExp(`--liquid-glass-${token}:\\s*([^;]+);`));
  if (!match) throw new Error(`Missing token ${token}`);
  return match[1].trim();
};
const defaults = {
  shape: value("shape"), scale: Number(value("scale")), aberration: Number(value("aberration")),
  bevelDepth: Number(value("bevel-depth")), frost: Number(value("frost")), tint: Number(value("tint")),
};
const hook = `/* GENERATED from design/tokens.css. Run node scripts/export-liquid-glass.cjs.
 * React/DialKit integration reference; not loaded by the native design specimen.
 */
"use client";
import { useDialKit } from "dialkit";

export function useLiquidGlassControls() {
  return useDialKit("Controls", {
    shape: { type: "select", options: ["circle", "pill"], default: ${JSON.stringify(defaults.shape)} },
    scale: [${defaults.scale}, 0, 100],
    aberration: [${defaults.aberration}, 0, 30],
    bevelDepth: [${defaults.bevelDepth}, 0, 100],
    frost: [${defaults.frost}, 0, 10, 0.1],
    tint: [${defaults.tint}, 0, 1, 0.01],
  });
}
`;
const hookPath = path.join(root, "design/tooling/liquid-glass-controls.tsx");
if (process.argv.includes("--check")) {
  if (fs.readFileSync(hookPath, "utf8") !== hook) throw new Error("DialKit defaults have drifted from tokens.css");
  console.log("DialKit Controls defaults match tokens.css.");
} else {
  const sourcePath = path.join(root, ".state/aceternity-glass/source-2.tsx");
  const source = fs.readFileSync(sourcePath, "utf8");
  const marker = "/** Signed distance to a rounded box";
  if (!source.includes(marker)) throw new Error("Unexpected upstream source");
  const native = "/* Adapted from Aceternity UI Liquid Glass, public Manual source.\n" +
    " * https://ui.aceternity.com/components/liquid-glass\n" +
    " * Provenance: tooling/aceternity-liquid-glass.json. */\n" +
    stripTypeScriptTypes(source.slice(source.indexOf(marker))).replace(/[ \t]+(?=\r?$)/gm, "") +
    "\nexport { generateDisplacementBlobUrl, buildFilterSvg };\n";
  const outputPath = path.join(root, "design/liquid-glass-optics.js");
  fs.writeFileSync(outputPath, native);
  fs.writeFileSync(hookPath, hook);
  const hash = input => crypto.createHash("sha256").update(input).digest("hex");
  fs.writeFileSync(path.join(root, "design/tooling/aceternity-liquid-glass.json"), JSON.stringify({
    component: "Aceternity UI Liquid Glass",
    url: "https://ui.aceternity.com/components/liquid-glass",
    terms_url: "https://ui.aceternity.com/licence",
    source_access: "Public Manual installation code", retrieved: "2026-10-22",
    source_snapshot_sha256: hash(source), source_snapshot_bytes: Buffer.byteLength(source),
    optics_sha256: hash(native),
    adaptation: "Original displacement-map and SVG filter helpers, TypeScript types and trailing whitespace removed; native lifecycle adapter; React useDialKit Controls reference generated from CSS tokens",
    scope: "Independent design specimen, no product stack selection or React runtime installed",
  }, null, 2) + "\n");
  console.log("Liquid Glass optics exported; Controls defaults generated from tokens.css.");
}
