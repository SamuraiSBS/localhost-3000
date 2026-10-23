/* Bayer/duotone algorithm adapted from Aceternity UI Dither Shader.
 * https://ui.aceternity.com/components/dither-shader
 * Scope and source snapshot: tooling/aceternity-dither.json.
 * Native Canvas 2D preview only; no React stack or animation loop.
 */
const BAYER = [
  [0, 8, 2, 10],
  [12, 4, 14, 6],
  [3, 11, 1, 9],
  [15, 7, 13, 5],
];
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

export class DitherPreview {
  constructor(stage, status, onState = () => {}) {
    this.stage = stage;
    this.status = status;
    this.onState = onState;
    this.image = stage.querySelector("img");
    this.canvas = stage.querySelector("canvas");
    this.source = document.createElement("canvas");
    this.redraw = () => this.render();
    this.failed = () => this.fallback("Не удалось загрузить изображение для обработки.");
    this.image.addEventListener("load", this.redraw);
    this.image.addEventListener("error", this.failed);
    this.observer = new ResizeObserver(this.redraw);
    this.observer.observe(stage);
    if (this.image.complete) this.render();
  }

  fallback(message = "Эффект недоступен. Показано исходное изображение.") {
    this.stage.dataset.renderer = "fallback";
    this.status.textContent = message;
    this.status.hidden = false;
    this.onState(false);
  }

  render() {
    if (!this.image.complete || !this.image.naturalWidth) return;
    const box = this.stage.getBoundingClientRect();
    const width = Math.floor(box.width), height = Math.floor(box.height);
    if (width < 1 || height < 1) return;
    try {
      const ctx = this.canvas.getContext("2d");
      const sourceCtx = this.source.getContext("2d", { willReadFrequently: true });
      if (!ctx || !sourceCtx) return this.fallback();
      const style = getComputedStyle(this.stage);
      const number = (token, low, high) => {
        const value = Number.parseFloat(style.getPropertyValue(token));
        if (!Number.isFinite(value)) throw new Error("Invalid dither token");
        return clamp(value, low, high);
      };
      const grid = number("--dither-grid-size", 1, 3);
      const threshold = number("--dither-threshold", 0, 1);
      const contrast = number("--dither-contrast", 0, 2);
      const brightness = number("--dither-brightness", -1, 1);
      const cell = Math.max(1, Math.floor(grid * number("--dither-pixel-ratio", 1, 4)));
      const background = style.getPropertyValue("--color-bg").trim();
      const dark = style.getPropertyValue("--dither-color-dark").trim();
      const light = style.getPropertyValue("--dither-color-light").trim();
      this.source.width = width; this.source.height = height;
      sourceCtx.fillStyle = background;
      sourceCtx.fillRect(0, 0, width, height);
      const imageFit = getComputedStyle(this.image).objectFit;
      if (imageFit === "cover" || imageFit === "contain") {
        const ratios = [width / this.image.naturalWidth, height / this.image.naturalHeight];
        const scale = imageFit === "cover" ? Math.max(...ratios) : Math.min(...ratios);
        const drawWidth = this.image.naturalWidth * scale;
        const drawHeight = this.image.naturalHeight * scale;
        sourceCtx.drawImage(this.image, (width - drawWidth) / 2, (height - drawHeight) / 2, drawWidth, drawHeight);
      } else {
        sourceCtx.drawImage(this.image, 0, 0, width, height);
      }
      const pixels = sourceCtx.getImageData(0, 0, width, height).data;
      const dpr = Math.min(devicePixelRatio || 1, 2);
      this.canvas.width = Math.round(width * dpr);
      this.canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      for (let y = 0; y < height; y += cell) {
        for (let x = 0; x < width; x += cell) {
          const index = (y * width + x) * 4;
          const r = clamp((pixels[index] - 128) * contrast + 128 + brightness * 255, 0, 255);
          const g = clamp((pixels[index + 1] - 128) * contrast + 128 + brightness * 255, 0, 255);
          const b = clamp((pixels[index + 2] - 128) * contrast + 128 + brightness * 255, 0, 255);
          const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
          let limit = BAYER[Math.floor(y / grid) % 4][Math.floor(x / grid) % 4] / 16;
          limit = limit * (1 - threshold) + threshold * 0.5;
          ctx.fillStyle = luminance < limit ? dark : light;
          ctx.fillRect(x, y, cell, cell);
        }
      }
      this.stage.dataset.renderer = "canvas";
      this.stage.dataset.gridSize = String(grid);
      this.stage.dataset.threshold = String(threshold);
      this.stage.dataset.renderCount = String(Number(this.stage.dataset.renderCount || 0) + 1);
      this.status.hidden = true;
      this.onState(true);
    } catch {
      this.fallback();
    }
  }

  destroy() {
    this.observer.disconnect();
    this.image.removeEventListener("load", this.redraw);
    this.image.removeEventListener("error", this.failed);
  }
}
