import { generateDisplacementBlobUrl, buildFilterSvg } from "./liquid-glass-optics.js";
import { readLiquidGlassPreset } from "./liquid-glass-preset.js";

let lensId = 0;
export class LiquidGlassMaterial {
  constructor(surface, notice, { radiusFromCSS = false } = {}) {
    this.surface = surface;
    this.notice = notice;
    this.radiusFromCSS = radiusFromCSS;
    this.host = surface.querySelector(".liquid-filter");
    this.settings = readLiquidGlassPreset();
    this.id = ++lensId;
    this.revision = 0;
    this.reduced = matchMedia("(prefers-reduced-transparency: reduce)");
    this.forced = matchMedia("(forced-colors: active)");
    this.redraw = () => { this.render(); };
    for (const query of [this.reduced, this.forced]) query.addEventListener("change", this.redraw);
    this.observer = new ResizeObserver(this.redraw);
    this.observer.observe(surface);
    this.render();
  }

  update(settings) {
    this.settings = { ...settings };
    this.render();
  }

  fallback(mode) {
    this.surface.style.removeProperty("backdrop-filter");
    this.surface.style.removeProperty("-webkit-backdrop-filter");
    this.surface.dataset.renderer = mode;
    this.surface.dataset.ready = "true";
    this.surface.dataset.applied = JSON.stringify(this.settings);
    if (this.notice) {
      this.notice.textContent = mode === "opaque"
        ? "Показана непрозрачная поверхность по настройкам доступности."
        : "Показано матовое стекло: SVG-преломление недоступно.";
      this.notice.hidden = false;
    }
  }

  async render() {
    const revision = ++this.revision;
    const settings = { ...this.settings };
    this.surface.dataset.shape = settings.shape;
    this.surface.style.setProperty("--liquid-glass-tint", settings.tint);
    this.surface.dataset.ready = "false";
    if (this.reduced.matches || this.forced.matches) { this.fallback("opaque"); return; }
    // Upstream documents SVG backdrop refraction as Chromium-only. Verify paint in QA.
    const chromium = /Chrome|Chromium|Edg|OPR/.test(navigator.userAgent);
    if (!chromium || !CSS.supports("backdrop-filter", "url(#liquid-test)")) { this.fallback("frosted"); return; }
    let candidateUrl;
    try {
      const bounds = this.surface.getBoundingClientRect();
      const width = Math.round(bounds.width), height = Math.round(bounds.height);
      if (width < 2 || height < 2) return;
      const maxRadius = Math.min(width, height) / 2;
      const radius = this.radiusFromCSS
        ? Math.min(maxRadius, parseFloat(getComputedStyle(this.surface).borderTopLeftRadius) || 0)
        : maxRadius;
      const mapUrl = candidateUrl = await generateDisplacementBlobUrl({ width, height, radius,
        bevelDepth: Math.min(settings.bevelDepth, radius), dpr: Math.min(devicePixelRatio || 1, 2) });
      await new Promise((resolve, reject) => {
        const image = new Image(); image.onload = resolve; image.onerror = reject; image.src = mapUrl;
      });
      if (revision !== this.revision || this.destroyed) { URL.revokeObjectURL(mapUrl); return; }
      const id = `liquid-${this.id}-${revision}`;
      this.host.innerHTML = buildFilterSvg({ id, mapUrl, width, height, scale: settings.scale,
        aberration: settings.aberration, frost: settings.frost });
      const backdrop = `url(#${id}) blur(0.5px) saturate(1.85) brightness(1.02)`;
      this.surface.style.backdropFilter = backdrop;
      this.surface.style.webkitBackdropFilter = backdrop;
      this.surface.dataset.renderer = "svg";
      this.surface.dataset.applied = JSON.stringify(settings);
      this.surface.dataset.ready = "true";
      this.surface.dataset.renderCount = String(Number(this.surface.dataset.renderCount || 0) + 1);
      if (this.notice) this.notice.hidden = true;
      if (this.mapUrl) URL.revokeObjectURL(this.mapUrl);
      this.mapUrl = mapUrl;
      candidateUrl = undefined;
    } catch {
      if (candidateUrl) URL.revokeObjectURL(candidateUrl);
      if (revision === this.revision && !this.destroyed) this.fallback("frosted");
    }
  }

  destroy() {
    this.destroyed = true;
    this.revision++;
    this.observer.disconnect();
    for (const query of [this.reduced, this.forced]) query.removeEventListener("change", this.redraw);
    if (this.mapUrl) URL.revokeObjectURL(this.mapUrl);
    this.host.replaceChildren();
  }
}
