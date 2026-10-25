/* Original native Canvas 2D interpretation of dotted contour currents.
 * It uses the documented visual controls without copying the ReactBits source.
 * Prop reference: https://pro.reactbits.dev/docs/components/chroma-current
 */
export const CHROMA_CURRENT_CONFIG = Object.freeze({
  pattern: "contours",
  colors: ["#FFFFFF", "#f00031"],
  centerX: 0.5,
  centerY: 0.5,
  scale: 1,
  pitch: 5,
  dotSize: 0.62,
  variance: 0.35,
  shape: "square",
  baseDots: 0,
  stretch: 0.2,
  streams: 9,
  spread: 0.25,
  speed: 0.6,
  direction: 0,
  turbulence: 0.1,
  trail: 0.6,
  intensity: 1,
  bloom: 0.9,
  backgroundColor: "#000000",
  grain: 0.2,
  stir: 1,
  plume: 1,
  interactive: false,
  intro: true,
  paused: false,
});

const clamp = (value, minimum = 0, maximum = 1) => Math.min(maximum, Math.max(minimum, value));
const fract = value => value - Math.floor(value);
const smoothstep = (edge0, edge1, value) => {
  const t = clamp((value - edge0) / (edge1 - edge0));
  return t * t * (3 - 2 * t);
};

function parseHex(value) {
  const hex = value.replace(/^#/, "");
  return [0, 2, 4].map(offset => Number.parseInt(hex.slice(offset, offset + 2), 16));
}

function makePalette(colors, steps = 32) {
  const [first, second] = colors.map(parseHex);
  return Array.from({ length: steps + 1 }, (_, index) => {
    const amount = index / steps;
    const color = first.map((channel, channelIndex) => Math.round(channel + (second[channelIndex] - channel) * amount));
    return `rgb(${color.join(" ")})`;
  });
}

function cellNoise(column, row) {
  let value = (Math.imul(column + 1, 374761393) + Math.imul(row + 1, 668265263)) | 0;
  value = Math.imul(value ^ (value >>> 13), 1274126177);
  value ^= value >>> 16;
  return (value >>> 0) / 4294967295;
}

export class ChromaCurrent {
  constructor(canvas, config = CHROMA_CURRENT_CONFIG) {
    this.canvas = canvas;
    this.config = config;
    this.context = canvas?.getContext("2d", { alpha: false }) ?? null;
    this.palette = makePalette(config.colors);
    this.background = `rgb(${parseHex(config.backgroundColor).join(" ")})`;
    this.dpr = 1;
    this.elapsed = 0;
    this.previousFrame = null;
    this.frameId = null;
    this.inViewport = true;
    this.pageActive = true;
    this.reducedMotion = false;

    this.requestFrame = now => {
      this.frameId = null;
      if (!this.shouldAnimate()) {
        this.render();
        return;
      }

      if (this.previousFrame !== null) {
        this.elapsed += Math.min(0.05, Math.max(0, now - this.previousFrame) / 1000);
      }
      this.previousFrame = now;
      this.render();
      this.frameId = window.requestAnimationFrame(this.requestFrame);
    };

    this.handleResize = () => {
      this.resize();
      this.syncAnimation();
    };
    this.handleVisibility = () => this.syncAnimation();
    this.handleMotionPreference = event => {
      this.reducedMotion = event.matches;
      this.syncAnimation();
    };

    if (!this.context) return;

    this.resizeObserver = typeof ResizeObserver === "function"
      ? new ResizeObserver(this.handleResize)
      : null;
    this.resizeObserver?.observe(canvas.parentElement ?? canvas);
    window.addEventListener("resize", this.handleResize, { passive: true });
    document.addEventListener("visibilitychange", this.handleVisibility);

    this.motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    this.reducedMotion = this.motionQuery.matches;
    if (this.motionQuery.addEventListener) {
      this.motionQuery.addEventListener("change", this.handleMotionPreference);
    } else {
      this.motionQuery.addListener?.(this.handleMotionPreference);
    }

    this.intersectionObserver = typeof IntersectionObserver === "function"
      ? new IntersectionObserver(([entry]) => {
        this.inViewport = entry.isIntersecting;
        this.syncAnimation();
      })
      : null;
    this.intersectionObserver?.observe(canvas);

    this.resize();
    this.syncAnimation();
  }

  shouldAnimate() {
    return Boolean(
      this.context
      && !this.config.paused
      && !this.reducedMotion
      && this.inViewport
      && this.pageActive
      && !document.hidden,
    );
  }

  setVisible(visible) {
    this.pageActive = visible;
    this.syncAnimation();
  }

  syncAnimation() {
    if (this.shouldAnimate()) {
      if (this.frameId === null) {
        this.previousFrame = null;
        this.frameId = window.requestAnimationFrame(this.requestFrame);
      }
      return;
    }

    if (this.frameId !== null) {
      window.cancelAnimationFrame(this.frameId);
      this.frameId = null;
    }
    this.previousFrame = null;
    this.render();
  }

  resize() {
    if (!this.context) return;
    const bounds = this.canvas.getBoundingClientRect();
    this.dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    this.canvas.width = Math.max(1, Math.round(bounds.width * this.dpr));
    this.canvas.height = Math.max(1, Math.round(bounds.height * this.dpr));
    this.width = this.canvas.width;
    this.height = this.canvas.height;
    this.cell = this.config.pitch * this.dpr;
  }

  render() {
    if (!this.context || !this.width || !this.height) return;

    const { context: ctx, config, width, height, cell } = this;
    const smallestDimension = Math.max(1, Math.min(width, height));
    const centerX = width * config.centerX;
    const centerY = height * config.centerY;
    const spinDirection = config.direction < 0 ? -1 : 1;
    const motionTime = this.reducedMotion ? 0 : this.elapsed * config.speed;
    const introProgress = this.reducedMotion || !config.intro
      ? 1
      : smoothstep(0, 1.25, this.elapsed);

    ctx.globalAlpha = 1;
    ctx.fillStyle = this.background;
    ctx.fillRect(0, 0, width, height);
    if (introProgress <= 0) return;

    const columns = Math.ceil(width / cell);
    const rows = Math.ceil(height / cell);
    const bandWidth = 0.014 + config.spread * 0.05;
    const tailWidth = bandWidth * (1.25 + config.trail * 1.8);
    const rotation = motionTime * 0.18 * spinDirection;
    const noiseAmount = config.grain * 0.22;
    const halfPitch = cell / 2;
    let previousColor = -1;

    for (let row = 0; row < rows; row += 1) {
      const y = row * cell + halfPitch;
      const ny = (y - centerY) / smallestDimension;

      for (let column = 0; column < columns; column += 1) {
        const x = column * cell + halfPitch;
        const nx = (x - centerX) / smallestDimension;
        const radius = Math.hypot(nx, ny) / Math.max(0.05, config.scale);
        const angle = Math.atan2(ny, nx);
        const landscape = radius
          + config.turbulence * (
            0.55 * Math.sin(angle * 2 + rotation)
            + 0.28 * Math.cos(angle * 3 - rotation * 0.72)
            + 0.17 * Math.sin(nx * 5 + ny * 4 + motionTime * 0.12)
          );
        const phase = fract(landscape * config.streams + motionTime * 0.08);
        const distance = Math.min(phase, 1 - phase);
        const contour = 1 - smoothstep(bandWidth * 0.35, bandWidth, distance);
        const tail = Math.exp(-((distance / tailWidth) ** 2)) * config.trail * config.bloom * 0.24;
        let brightness = clamp((contour + tail) * config.intensity);

        if (brightness < 0.035 && config.baseDots === 0) continue;
        brightness = Math.max(brightness, config.baseDots);

        const grain = cellNoise(column, row);
        brightness = clamp(brightness * (1 + (grain - 0.5) * noiseAmount));
        const colorMix = clamp(
          0.5 + 0.5 * Math.sin(landscape * 8 + angle * 0.6 + motionTime * 0.1),
        );
        const colorIndex = Math.round(colorMix * (this.palette.length - 1));
        if (colorIndex !== previousColor) {
          ctx.fillStyle = this.palette[colorIndex];
          previousColor = colorIndex;
        }

        const sizeFactor = (1 - config.variance) + config.variance * Math.sqrt(brightness);
        const bloomScale = 1 + config.bloom * 0.12 * brightness;
        const dotSize = cell * config.dotSize * sizeFactor * bloomScale;
        const dotWidth = dotSize * (1 + config.stretch * 0.4 * brightness);
        const dotHeight = dotSize * (1 - config.stretch * 0.3 * brightness);
        ctx.globalAlpha = clamp(brightness * introProgress);
        if (config.shape === "square") {
          ctx.fillRect(x - dotWidth / 2, y - dotHeight / 2, dotWidth, dotHeight);
        } else {
          ctx.beginPath();
          ctx.ellipse(x, y, dotWidth / 2, dotHeight / 2, 0, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }

    ctx.globalAlpha = 1;
  }

  destroy() {
    if (this.frameId !== null) window.cancelAnimationFrame(this.frameId);
    this.frameId = null;
    this.resizeObserver?.disconnect();
    this.intersectionObserver?.disconnect();
    window.removeEventListener("resize", this.handleResize);
    document.removeEventListener("visibilitychange", this.handleVisibility);
    if (this.motionQuery?.removeEventListener) {
      this.motionQuery.removeEventListener("change", this.handleMotionPreference);
    } else {
      this.motionQuery?.removeListener?.(this.handleMotionPreference);
    }
  }
}
