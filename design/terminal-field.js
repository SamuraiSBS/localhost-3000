/*
 * FaultyTerminal WebGL renderer adapted from React Bits for the existing HTML site.
 * Copyright (c) 2026 David Haz.
 * MIT + Commons Clause License Condition v1.0.
 * Permission is hereby granted, free of charge, to any person obtaining a copy of
 * this software and associated documentation files (the Software), to deal in the
 * Software without restriction, including without limitation the rights to use,
 * copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the
 * Software, subject to the following conditions:
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 * Commons Clause: do not sell, sublicense, or redistribute the component itself,
 * whether alone, in a bundle, or as a ported version.
 * No warranty. Full terms: docs/design/THIRD_PARTY_NOTICES.md.
 * Source: https://github.com/DavidHDev/react-bits
 */

const INTRO_SECONDS = 2;
const MAX_CLICKS = 4;
const CLICK_SECONDS = { glitch: 0.7, degauss: 1.6 };
const GLYPHS = { blocks: 0, dots: 1, characters: 2 };
const CLICKS = { none: 0, glitch: 1, degauss: 2 };

const FONT = [
  ['.###.', '#..##', '#.#.#', '##..#', '.###.'],
  ['..#..', '.##..', '..#..', '..#..', '.###.'],
  ['####.', '....#', '.###.', '#....', '#####'],
  ['####.', '....#', '.###.', '....#', '####.'],
  ['#..#.', '#..#.', '#####', '...#.', '...#.'],
  ['#####', '#....', '####.', '....#', '####.'],
  ['.###.', '#....', '####.', '#...#', '.###.'],
  ['#####', '....#', '...#.', '..#..', '..#..'],
  ['.###.', '#...#', '.###.', '#...#', '.###.'],
  ['.###.', '#...#', '.####', '....#', '.###.'],
  ['.###.', '#...#', '#####', '#...#', '#...#'],
  ['####.', '#...#', '####.', '#...#', '####.'],
  ['.####', '#....', '#....', '#....', '.####'],
  ['####.', '#...#', '#...#', '#...#', '####.'],
  ['#####', '#....', '####.', '#....', '#####'],
  ['#####', '#....', '####.', '#....', '#....'],
  ['.....', '..#..', '.###.', '..#..', '.....'],
  ['.....', '.....', '.###.', '.....', '.....'],
  ['.#...', '..#..', '...#.', '..#..', '.#...'],
  ['....#', '...#.', '..#..', '.#...', '#....'],
  ['.....', '#.#.#', '.###.', '#.#.#', '.....'],
  ['.....', '#####', '.....', '#####', '.....'],
  ['.....', '.....', '.....', '.....', '#####']
];

const FONT_BITS = FONT.map(rows =>
  rows.reduce((bits, row, r) => {
    let value = bits;
    for (let c = 0; c < 5; c++) if (row[c] === '#') value += 2 ** (r * 5 + c);
    return value;
  }, 0)
);

const VERTEX = `#version 300 es
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

const PATTERN = `
float time;

float noise(vec2 p) {
  return sin(p.x * 10.0) * sin(p.y * (3.0 + sin(time * 0.090909))) + 0.2;
}

mat2 rotate(float angle) {
  float c = cos(angle);
  float s = sin(angle);
  return mat2(c, -s, s, c);
}

float fbm(vec2 p) {
  p *= 1.1;
  float f = 0.0;
  float amp = 0.5 * uNoiseAmp;
  mat2 modify0 = rotate(time * 0.02);
  f += amp * noise(p);
  p = modify0 * p * 2.0;
  amp *= 0.454545;
  mat2 modify1 = rotate(time * 0.02);
  f += amp * noise(p);
  p = modify1 * p * 2.0;
  amp *= 0.454545;
  f += amp * noise(p);
  return f;
}

float pattern(vec2 p) {
  vec2 offset1 = vec2(1.0);
  vec2 offset0 = vec2(0.0);
  mat2 rot01 = rotate(0.1 * time);
  mat2 rot1 = rotate(0.1);
  vec2 q = vec2(fbm(p + offset1), fbm(rot01 * p + offset1));
  vec2 r = vec2(fbm(rot1 * q + offset0), fbm(q + offset0));
  return fbm(p + r);
}
`;

const CELL_FRAGMENT = `#version 300 es
precision highp float;
precision highp int;

uniform float iTime;
uniform vec2 uGridMul;
uniform float uNoiseAmp;
uniform float uScale;
uniform vec2 uMouse;
uniform float uMouseStrength;
uniform float uUseMouse;
uniform float uPageLoadProgress;
uniform float uUsePageLoadAnimation;
uniform ivec2 uOrigin;
uniform sampler2D uPrev0;
uniform sampler2D uPrev1;
uniform float uDecay;
uniform float uFresh;
uniform float uHover;

layout(location = 0) out vec4 outRings;
layout(location = 1) out vec4 outExtra;
${PATTERN}
void main() {
  time = iTime * 0.333333;
  ivec2 texel = ivec2(gl_FragCoord.xy);
  vec2 grid = uGridMul * 15.0;
  vec2 s = vec2(texel + uOrigin) / grid;
  float intensity = pattern(s * 0.1) * 1.3 - 0.03;
  float lift = 0.0;
  float strike = 0.0;

  if (uUseMouse > 0.5) {
    vec2 mouseWorld = uMouse * uScale;
    float distToMouse = distance(s, mouseWorld);
    float mouseInfluence = exp(-distToMouse * 8.0) * uMouseStrength * 10.0;
    intensity += mouseInfluence;
    float ripple = sin(distToMouse * 20.0 - iTime * 5.0) * 0.1 * mouseInfluence;
    intensity += ripple;
    lift = mouseInfluence;
  }

  if (uUsePageLoadAnimation > 0.5) {
    float cellRandom = fract(sin(dot(s, vec2(12.9898, 78.233))) * 43758.5453);
    float cellDelay = cellRandom * 0.8;
    float cellProgress = clamp((uPageLoadProgress - cellDelay) / 0.2, 0.0, 1.0);
    float fadeAlpha = smoothstep(0.0, 1.0, cellProgress);
    intensity *= fadeAlpha;
    strike = sin(3.14159265 * cellProgress) * step(0.1, intensity);
  }

  vec4 on0 = step(vec4(0.1), intensity - vec4(0.0, 1.0, 2.0, 4.0) * 0.0625);
  vec2 on1 = step(vec2(0.1), intensity - vec2(5.0, 8.0) * 0.0625);
  float level = clamp((intensity - 0.1) / 0.6, 0.0, 1.0);
  float heat = max(clamp(lift / 3.0, 0.0, 1.0) * uHover, strike);

  vec4 prev0 = texelFetch(uPrev0, texel, 0) * (1.0 - uFresh);
  vec4 prev1 = texelFetch(uPrev1, texel, 0) * (1.0 - uFresh);
  vec4 fade0 = max(prev0 * uDecay - 1.0 / 255.0, 0.0);
  vec4 fade1 = max(prev1 * uDecay - 1.0 / 255.0, 0.0);

  outRings = max(on0, fade0);
  outExtra = vec4(max(on1, fade1.xy), max(level, fade1.z), max(heat, fade1.w));
}`;

const fontArray = `const uint FONT[${FONT_BITS.length}] = uint[${FONT_BITS.length}](${FONT_BITS.map(
  bits => `${bits}u`
).join(', ')});`;

const MAIN_FRAGMENT = `#version 300 es
precision highp float;
precision highp int;

uniform float iTime;
uniform vec3 iResolution;
uniform float uScale;
uniform vec2 uGridMul;
uniform float uDigitSize;
uniform float uScanlineIntensity;
uniform float uGlitchAmount;
uniform float uFlickerAmount;
uniform float uChromaticAberration;
uniform float uDither;
uniform float uCurvature;
uniform vec3 uTint;
uniform float uBrightness;
uniform float uLightMode;
uniform sampler2D uRings0;
uniform sampler2D uRings1;
uniform ivec2 uOrigin;
uniform ivec2 uCellCount;
uniform int uGlyphs;
uniform float uGlow;
uniform float uScramble;
uniform float uMouseGlow;
uniform float uVignette;
uniform vec4 uClicks[${MAX_CLICKS}];
uniform int uClickMode;
uniform float uClickSeconds;
uniform float uClock;

out vec4 outColor;

${fontArray}

float time;
float clickTear;
float clickSplit;
float pixelHeat;

float hash21(vec2 p) {
  p = fract(p * 234.56);
  p += dot(p, p + 34.56);
  return fract(p.x * p.y);
}

float hashCell(vec2 p) {
  vec3 q = fract(vec3(p.xyx) * 0.1031);
  q += dot(q, q.yzx + 33.33);
  return fract((q.x + q.y) * q.z);
}

ivec2 cellTexel(vec2 cell) {
  return clamp(ivec2(cell) - uOrigin, ivec2(0), uCellCount - 1);
}

float ringValue(float n, vec4 r0, vec4 r1) {
  if (n < 0.5) return r0.x;
  if (n < 1.5) return r0.y;
  if (n < 2.5) return r0.z;
  if (n < 4.5) return r0.w;
  if (n < 5.5) return r1.x;
  return r1.y;
}

float digit(vec2 p) {
  vec2 grid = uGridMul * 15.0;
  vec2 cell = floor(p * grid);
  ivec2 texel = cellTexel(cell);
  vec4 r0 = texelFetch(uRings0, texel, 0);
  vec4 r1 = texelFetch(uRings1, texel, 0);
  float boost = 1.0 + r1.w * uMouseGlow * 1.3;
  pixelHeat = max(pixelHeat, r1.w);
  p = fract(p * grid);
  p *= uDigitSize;

  float px5 = p.x * 5.0;
  float py5 = (1.0 - p.y) * 5.0;
  float x = fract(px5);
  float y = fract(py5);
  float i = floor(py5) - 2.0;
  float j = floor(px5) - 2.0;
  float inside = step(0.0, p.x) * step(p.x, 1.0) * step(0.0, p.y) * step(p.y, 1.0);

  if (uGlyphs == 2) {
    float rate = 1.2 + r1.w * uScramble * 18.0;
    float phase = hashCell(cell * 1.37 + 4.1);
    float epoch = floor(iTime * rate + phase * 7.0);
    float pick = hashCell(cell + vec2(epoch * 0.731, epoch * 1.913));
    uint glyph = FONT[int(min(pick * float(FONT.length()), float(FONT.length()) - 1.0))];
    int bit = int(i + 2.0) * 5 + int(j + 2.0);
    float lit = float((glyph >> uint(clamp(bit, 0, 24))) & 1u);
    float glow = max(r0.x, r1.z);
    float shade = 0.35 + 0.65 * r1.z;
    return inside * lit * glow * shade * boost * (0.2 + y * 0.8) * (0.75 + x * 0.25);
  }

  float n = i * i + j * j;
  float on = ringValue(n, r0, r1);
  if (uGlyphs == 1) {
    vec2 d = vec2(x, y) - 0.5;
    float radius = 0.36 + 0.06 * on;
    float edge = fwidth(px5) * 0.75;
    float dot = 1.0 - smoothstep(radius - edge, radius + edge, length(d));
    return inside * on * dot * boost * (0.55 + 0.45 * (1.0 - length(d) / 0.5));
  }
  float brightness = on * (0.2 + y * 0.8) * (0.75 + x * 0.25);
  return inside * brightness * boost;
}

float onOff(float a, float b, float c) {
  return step(c, sin(iTime + a * cos(iTime * b))) * uFlickerAmount;
}

float displace(vec2 look) {
  float y = look.y - mod(iTime * 0.25, 1.0);
  float window = 1.0 / (1.0 + 50.0 * y * y);
  return sin(look.y * 20.0 + iTime) * 0.0125 * onOff(4.0, 2.0, 0.8) * (1.0 + cos(iTime * 60.0)) * window;
}

vec2 getLight(vec2 p) {
  float bar = step(mod(p.y + time * 20.0, 1.0), 0.2) * 0.4 + 1.0;
  bar *= uScanlineIntensity;

  float displacement = displace(p);
  p.x += displacement;

  if (uGlitchAmount != 1.0) {
    float extra = displacement * (uGlitchAmount - 1.0);
    p.x += extra;
  }

  p.x += clickTear;

  float middle = digit(p);

  const float off = 0.002;
  float sum = digit(p + vec2(-off, -off)) + digit(p + vec2(0.0, -off)) + digit(p + vec2(off, -off)) +
              digit(p + vec2(-off, 0.0)) + digit(p + vec2(0.0, 0.0)) + digit(p + vec2(off, 0.0)) +
              digit(p + vec2(-off, off)) + digit(p + vec2(0.0, off)) + digit(p + vec2(off, off));

  return vec2(0.9 * middle, sum * 0.1 * bar);
}

float glowAt(vec2 p) {
  vec2 grid = uGridMul * 15.0;
  vec2 cell = floor(p * grid);
  vec2 cellPx = iResolution.xy / (uScale * grid);
  float sigma = 0.5 * min(cellPx.x, cellPx.y);
  float center = 0.5 * min(1.0, 1.0 / max(uDigitSize, 0.001));
  float total = 0.0;
  for (int oy = -1; oy <= 1; oy++) {
    for (int ox = -1; ox <= 1; ox++) {
      vec2 c = cell + vec2(float(ox), float(oy));
      ivec2 texel = cellTexel(c);
      vec4 r0 = texelFetch(uRings0, texel, 0);
      vec4 r1 = texelFetch(uRings1, texel, 0);
      float emission = uGlyphs == 2
        ? max(r0.x, r1.z) * (0.35 + 0.65 * r1.z) * 0.45
        : (r0.x + 4.0 * (r0.y + r0.z + r0.w + r1.y) + 8.0 * r1.x) / 25.0;
      emission *= 1.0 + r1.w * uMouseGlow * 1.6;
      vec2 d = (p - (c + center) / grid) * iResolution.xy / uScale;
      total += emission * exp(-dot(d, d) / (2.0 * sigma * sigma));
    }
  }
  return total;
}

vec2 barrel(vec2 uv) {
  vec2 c = uv * 2.0 - 1.0;
  float r2 = dot(c, c);
  c *= 1.0 + uCurvature * r2;
  return c * 0.5 + 0.5;
}

void main() {
  time = iTime * 0.333333;
  vec2 uv = gl_FragCoord.xy / iResolution.xy;

  if (uCurvature != 0.0) {
    uv = barrel(uv);
  }

  clickTear = 0.0;
  clickSplit = 0.0;
  if (uClickMode > 0) {
    for (int k = 0; k < ${MAX_CLICKS}; k++) {
      vec4 click = uClicks[k];
      if (click.w <= 0.0) continue;
      float age = uClock - click.z;
      if (age < 0.0 || age > uClickSeconds) continue;
      float life = age / uClickSeconds;
      float envelope = (1.0 - life) * (1.0 - life);
      if (uClickMode == 1) {
        float tick = floor(age * 30.0);
        float bands = 14.0 + 26.0 * hashCell(vec2(click.z, tick * 0.37));
        float row = floor(uv.y * bands);
        float pickRow = hashCell(vec2(row + tick * 7.0, click.z * 17.0));
        float torn = step(0.42, pickRow);
        float reach = exp(-abs(uv.y - click.y) * 2.5);
        float shift = (hashCell(vec2(row * 1.7 + tick, click.z)) - 0.5) * 0.22;
        clickTear += shift * torn * envelope * reach;
        clickSplit = max(clickSplit, envelope * reach * (0.4 + 0.6 * torn));
      } else {
        vec2 aspect = vec2(iResolution.x / iResolution.y, 1.0);
        vec2 delta = (uv - click.xy) * aspect;
        float dist = length(delta);
        float front = age * 1.6;
        float band = exp(-pow((dist - front) / 0.18, 2.0));
        float wave = sin((dist - front) * 40.0) * band;
        uv += delta / aspect * wave * 0.05 * envelope;
        clickSplit = max(clickSplit, envelope * band);
      }
    }
  }

  vec2 p = uv * uScale;
  pixelHeat = 0.0;
  vec2 lightG = getLight(p);
  float heat = pixelHeat;
  vec2 lightR = lightG;
  vec2 lightB = lightG;

  float split = uChromaticAberration + clickSplit * 6.0;
  if (split != 0.0) {
    vec2 ca = vec2(split) / iResolution.xy;
    lightR = getLight(p + ca);
    lightB = getLight(p - ca);
  }

  if (uGlow > 0.0) {
    float bloom = uGlow * 0.2 * glowAt(p);
    lightR.y += bloom;
    lightG.y += bloom;
    lightB.y += bloom;
  }

  vec3 core = vec3(lightR.x, lightG.x, lightB.x);
  vec3 halo = vec3(lightR.y, lightG.y, lightB.y);
  vec3 raw = core + halo;

  vec3 luma = vec3(0.2126, 0.7152, 0.0722);
  float peak = max(uTint.r, max(uTint.g, uTint.b));
  float phosphor = clamp(uGlow, 0.0, 1.0);
  vec3 vividTint = clamp(mix(vec3(dot(uTint, luma)), uTint, 1.0 + 0.8 * phosphor), 0.0, 1.0);
  vec3 coreTint = mix(uTint, vec3(peak), phosphor * 0.45 * smoothstep(0.45, 0.9, core));
  vec3 col = core * coreTint + halo * vividTint;
  col += (vec3(1.0) - uTint) * min(raw, vec3(1.0)) * heat * uMouseGlow * 0.6;
  col *= uBrightness;

  if (uVignette > 0.0) {
    float edge = 0.04 + 0.22 * uVignette;
    vec2 fade = smoothstep(vec2(0.0), vec2(edge), uv) * smoothstep(vec2(0.0), vec2(edge), 1.0 - uv);
    float mask = fade.x * fade.y;
    mask = mask * mask * (3.0 - 2.0 * mask);
    float keep = mix(1.0, mask, clamp(uVignette * 1.5, 0.0, 1.0));
    col *= keep;
    core *= keep;
    halo *= keep;
  }

  if (uLightMode > 0.5) {
    vec3 luma = vec3(0.2126, 0.7152, 0.0722);
    float lum = dot(uTint, luma);
    float peak = max(uTint.r, max(uTint.g, uTint.b));
    vec3 hue = uTint / max(peak, 0.0001);
    vec3 vivid = clamp(mix(vec3(dot(hue, luma)), hue, 1.6), 0.0, 1.0);
    vivid = clamp(vivid * (0.3 / max(dot(vivid, luma), 0.0001)), 0.0, 1.0);
    vec3 ink = mix(uTint, vivid, smoothstep(0.3, 0.6, lum));
    vec3 cover = (core * 1.15 + halo * 0.4) * uBrightness * (1.0 + heat * uMouseGlow * 0.4);
    if (uDither > 0.0) cover += (hash21(gl_FragCoord.xy) - 0.5) * (uDither * 0.003922);
    cover = clamp(cover, 0.0, 0.94);
    float alpha = max(cover.r, max(cover.g, cover.b));
    outColor = vec4(alpha - cover * (1.0 - ink), alpha);
    return;
  }

  if (uDither > 0.0) {
    float rnd = hash21(gl_FragCoord.xy);
    col += (rnd - 0.5) * (uDither * 0.003922);
  }

  col = clamp(col, 0.0, 1.0);
  outColor = vec4(col, max(col.r, max(col.g, col.b)));
}`;

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const compile = (gl, type, source) => {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader;
  gl.deleteShader(shader);
  return null;
};

const link = (gl, fragmentSource) => {
  const vertex = compile(gl, gl.VERTEX_SHADER, VERTEX);
  const fragment = compile(gl, gl.FRAGMENT_SHADER, fragmentSource);
  const program = gl.createProgram();
  if (!vertex || !fragment || !program) return null;
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    gl.deleteProgram(program);
    return null;
  }
  const uniforms = {};
  const count = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < count; i++) {
    const info = gl.getActiveUniform(program, i);
    if (!info) continue;
    const name = info.name.replace(/\[0\]$/, '');
    uniforms[name] = gl.getUniformLocation(program, name);
  }
  return { program, uniforms };
};

const hexToRgb = hex => {
  let h = String(hex || '#ffffff')
    .replace('#', '')
    .trim();
  if (h.length === 3 || h.length === 4)
    h = h
      .slice(0, 3)
      .split('')
      .map(c => c + c)
      .join('');
  const num = parseInt(h.slice(0, 6), 16);
  if (Number.isNaN(num)) return [1, 1, 1];
  return [((num >> 16) & 255) / 255, ((num >> 8) & 255) / 255, (num & 255) / 255];
};


function readNumber(style, name, fallback) {
  const value = Number.parseFloat(style.getPropertyValue(name));
  return Number.isFinite(value) ? value : fallback;
}

function mountFaultyTerminal() {
  const canvas = document.querySelector(".terminal-field");
  if (!(canvas instanceof HTMLCanvasElement)) return;

  const gl = canvas.getContext("webgl2", {
    alpha: true,
    premultipliedAlpha: true,
    antialias: false,
    depth: false,
    stencil: false
  });
  if (!gl || gl.getParameter(gl.MAX_COLOR_ATTACHMENTS) < 2 || gl.getParameter(gl.MAX_DRAW_BUFFERS) < 2) {
    canvas.hidden = true;
    return;
  }

  const cellShader = link(gl, CELL_FRAGMENT);
  const mainShader = link(gl, MAIN_FRAGMENT);
  const vao = gl.createVertexArray();
  if (!cellShader || !mainShader || !vao) {
    canvas.hidden = true;
    return;
  }

  const style = getComputedStyle(document.documentElement);
  const read = (name, fallback) => readNumber(style, name, fallback);
  const settings = {
    scale: Math.max(0.05, read("--terminal-field-scale", 2.4)),
    gridX: Math.max(0.1, read("--terminal-field-grid-x", 2) * 15),
    gridY: Math.max(0.1, read("--terminal-field-grid-y", 1) * 15),
    digitSize: read("--terminal-field-digit-size", 1.1),
    timeScale: read("--terminal-field-time-scale", 0.5),
    pause: false,
    scanlineIntensity: read("--terminal-field-scanline-intensity", 0.3),
    glitchAmount: read("--terminal-field-glitch-amount", 1.1),
    flickerAmount: read("--terminal-field-flicker-amount", 1),
    noiseAmp: read("--terminal-field-noise-amp", 1),
    chromaticAberration: read("--terminal-field-chromatic-aberration", 0),
    dither: read("--terminal-field-dither", 0),
    curvature: read("--terminal-field-curvature", 0),
    tint: style.getPropertyValue("--terminal-field-ink").trim() || "#E4E4E7",
    mouseReact: read("--terminal-field-mouse-react", 0) > 0.5,
    mouseStrength: read("--terminal-field-mouse-strength", 0.5),
    pageLoadAnimation: read("--terminal-field-page-load-animation", 1) > 0.5,
    brightness: read("--terminal-field-brightness", 0.7),
    glyphs: GLYPHS[style.getPropertyValue("--terminal-field-glyphs").trim()] ?? GLYPHS.characters,
    glow: clamp(read("--terminal-field-glow", 1), 0, 3),
    persistence: clamp(read("--terminal-field-persistence", 0.37), 0, 1),
    scramble: clamp(read("--terminal-field-scramble", 0.9), 0, 1),
    mouseGlow: clamp(read("--terminal-field-mouse-glow", 0.6), 0, 1),
    vignette: clamp(read("--terminal-field-vignette", 0.25), 0, 1),
    clickEffect: CLICKS[style.getPropertyValue("--terminal-field-click-effect").trim()] ?? CLICKS.none,
    clickSeconds: 1,
    variation: undefined,
    lightMode: false,
    dpr: 0
  };

  const cu = cellShader.uniforms;
  const mu = mainShader.uniforms;
  const targets = [];
  const makeTexture = () => {
    const texture = gl.createTexture();
    if (!texture) return null;
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    return texture;
  };

  for (let index = 0; index < 2; index += 1) {
    const rings = makeTexture();
    const extra = makeTexture();
    const framebuffer = gl.createFramebuffer();
    if (!rings || !extra || !framebuffer) {
      canvas.hidden = true;
      gl.deleteTexture(rings);
      gl.deleteTexture(extra);
      gl.deleteFramebuffer(framebuffer);
      gl.deleteVertexArray(vao);
      gl.deleteProgram(cellShader.program);
      gl.deleteProgram(mainShader.program);
      return;
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, rings, 0);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT1, gl.TEXTURE_2D, extra, 0);
    targets.push({ rings, extra, framebuffer });
  }
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  let reduce = motionQuery.matches;
  const view = { width: 1, height: 1, ratio: 1, columns: 1 };
  const cells = { x: 0, y: 0, w: 1, h: 1, key: "" };
  const mouse = { x: 0.5, y: 0.5 };
  const clicks = new Float32Array(MAX_CLICKS * 4);
  let clock = 0;
  let hover = 0;
  let current = 0;
  let fresh = true;
  let iTime = (typeof settings.variation === "number" ? settings.variation * 10 : Math.random() * 100)
    * settings.timeScale;
  let introClock = settings.pageLoadAnimation && !reduce ? 0 : 1;
  let raf = 0;
  let last = 0;
  let visible = true;
  let quiet = 0;

  const layoutCells = () => {
    const reach = 1 + Math.max(0, settings.curvature) * 2;
    const uvMin = 0.5 - 0.5 * reach;
    const uvMax = 0.5 + 0.5 * reach;
    const margin = 0.06 * Math.max(1, Math.abs(settings.glitchAmount))
      * Math.max(1, Math.abs(settings.flickerAmount)) + 0.2;
    const gridX = settings.gridX * view.columns;
    const x0 = Math.floor((uvMin * settings.scale - margin) * gridX) - 2;
    const x1 = Math.floor((uvMax * settings.scale + margin) * gridX) + 2;
    const y0 = Math.floor((uvMin * settings.scale - margin) * settings.gridY) - 2;
    const y1 = Math.floor((uvMax * settings.scale + margin) * settings.gridY) + 2;
    const key = [x0, x1, y0, y1].join(":");
    if (key === cells.key) return;
    cells.key = key;
    cells.x = x0;
    cells.y = y0;
    cells.w = Math.max(1, Math.min(4096, x1 - x0 + 1));
    cells.h = Math.max(1, Math.min(4096, y1 - y0 + 1));
    for (const target of targets) {
      for (const texture of [target.rings, target.extra]) {
        gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, cells.w, cells.h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      }
    }
    fresh = true;
  };

  const stepCells = decay => {
    const source = targets[current];
    const target = targets[1 - current];
    gl.bindFramebuffer(gl.FRAMEBUFFER, target.framebuffer);
    gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1]);
    gl.viewport(0, 0, cells.w, cells.h);
    gl.disable(gl.BLEND);
    gl.useProgram(cellShader.program);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, source.rings);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, source.extra);
    gl.uniform1i(cu.uPrev0, 0);
    gl.uniform1i(cu.uPrev1, 1);
    gl.uniform1f(cu.iTime, iTime);
    gl.uniform2f(cu.uGridMul, (settings.gridX * view.columns) / 15, settings.gridY / 15);
    gl.uniform1f(cu.uNoiseAmp, settings.noiseAmp);
    gl.uniform1f(cu.uScale, settings.scale);
    gl.uniform2f(cu.uMouse, mouse.x, mouse.y);
    gl.uniform1f(cu.uMouseStrength, settings.mouseStrength);
    gl.uniform1f(cu.uUseMouse, settings.mouseReact ? 1 : 0);
    gl.uniform1f(cu.uPageLoadProgress, introClock);
    gl.uniform1f(cu.uUsePageLoadAnimation, settings.pageLoadAnimation ? 1 : 0);
    gl.uniform2i(cu.uOrigin, cells.x, cells.y);
    gl.uniform1f(cu.uDecay, decay);
    gl.uniform1f(cu.uFresh, fresh ? 1 : 0);
    gl.uniform1f(cu.uHover, hover);
    gl.bindVertexArray(vao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.bindVertexArray(null);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    current = 1 - current;
    fresh = false;
  };

  const draw = () => {
    const source = targets[current];
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(mainShader.program);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, source.rings);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, source.extra);
    gl.uniform1i(mu.uRings0, 0);
    gl.uniform1i(mu.uRings1, 1);
    gl.uniform1f(mu.iTime, iTime);
    gl.uniform3f(mu.iResolution, canvas.width, canvas.height, canvas.width / canvas.height);
    gl.uniform1f(mu.uScale, settings.scale);
    gl.uniform2f(mu.uGridMul, (settings.gridX * view.columns) / 15, settings.gridY / 15);
    gl.uniform1f(mu.uDigitSize, settings.digitSize);
    gl.uniform1f(mu.uScanlineIntensity, settings.scanlineIntensity);
    gl.uniform1f(mu.uGlitchAmount, reduce ? 0 : settings.glitchAmount);
    gl.uniform1f(mu.uFlickerAmount, reduce ? 0 : settings.flickerAmount);
    gl.uniform1f(mu.uChromaticAberration, settings.chromaticAberration);
    gl.uniform1f(mu.uDither, settings.dither);
    gl.uniform1f(mu.uCurvature, settings.curvature);
    gl.uniform3fv(mu.uTint, hexToRgb(settings.tint));
    gl.uniform1f(mu.uBrightness, settings.brightness);
    gl.uniform1f(mu.uLightMode, settings.lightMode ? 1 : 0);
    gl.uniform2i(mu.uOrigin, cells.x, cells.y);
    gl.uniform2i(mu.uCellCount, cells.w, cells.h);
    gl.uniform1i(mu.uGlyphs, settings.glyphs);
    gl.uniform1f(mu.uGlow, settings.glow);
    gl.uniform1f(mu.uScramble, settings.scramble);
    gl.uniform1f(mu.uMouseGlow, settings.mouseGlow);
    gl.uniform1f(mu.uVignette, settings.vignette);
    gl.uniform4fv(mu.uClicks, clicks);
    gl.uniform1i(mu.uClickMode, settings.clickEffect);
    gl.uniform1f(mu.uClickSeconds, settings.clickSeconds);
    gl.uniform1f(mu.uClock, clock);
    gl.bindVertexArray(vao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.bindVertexArray(null);
  };

  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    last = 0;
  }

  function drawStatic() {
    layoutCells();
    stepCells(0);
    draw();
  }

  function resize() {
    view.width = Math.max(1, window.innerWidth);
    view.height = Math.max(1, window.innerHeight);
    view.ratio = Math.min(settings.dpr || window.devicePixelRatio || 1, 2);
    view.columns = Math.min(1, view.width / view.height / 1.5);
    canvas.width = Math.max(1, Math.round(view.width * view.ratio));
    canvas.height = Math.max(1, Math.round(view.height * view.ratio));
    layoutCells();
    stepCells(0);
    draw();
    wake();
  }

  function frame(now) {
    raf = 0;
    if (!visible || document.hidden) return;
    const dt = last ? clamp((now - last) / 1000, 0.001, 1 / 20) : 1 / 60;
    last = now;
    clock += dt;
    if (!settings.pause && !reduce) iTime += dt * settings.timeScale;
    const targetX = 0.5;
    const targetY = 0.5;
    const follow = 1 - Math.exp(-dt / 0.6);
    mouse.x += (targetX - mouse.x) * follow;
    mouse.y += (targetY - mouse.y) * follow;
    hover += (0 - hover) * (1 - Math.exp(-dt / 0.35));
    layoutCells();
    const decay = settings.persistence > 0
      ? Math.exp(-dt / (0.08 + settings.persistence * 1.4))
      : 0;
    stepCells(decay);
    draw();
    if (introClock < 1) introClock = Math.min(1, introClock + dt / INTRO_SECONDS);
    let busy = !(settings.pause || reduce) || introClock < 1;
    busy = busy || Math.abs(targetX - mouse.x) + Math.abs(targetY - mouse.y) > 0.0001;
    busy = busy || Math.abs(hover) > 0.001;
    for (let index = 0; index < MAX_CLICKS; index += 1) {
      busy = busy || clicks[index * 4 + 3] > 0;
    }
    quiet = busy ? 0 : quiet + dt;
    if (quiet < 2.5) raf = requestAnimationFrame(frame);
  }

  function wake() {
    quiet = 0;
    if (document.hidden || !visible) return;
    if (reduce) {
      stop();
      drawStatic();
    } else if (!raf) {
      last = 0;
      raf = requestAnimationFrame(frame);
    }
  }

  function onVisibility() {
    if (document.hidden) stop();
    else wake();
  }

  function onMotionChange(event) {
    reduce = event.matches;
    if (reduce) {
      stop();
      introClock = 1;
      fresh = true;
      drawStatic();
    } else {
      wake();
    }
  }

  const onContextLost = event => {
    event.preventDefault();
    stop();
    canvas.hidden = true;
  };

  canvas.addEventListener("webglcontextlost", onContextLost, { once: true });
  window.addEventListener("resize", resize, { passive: true });
  document.addEventListener("visibilitychange", onVisibility);
  if (motionQuery.addEventListener) motionQuery.addEventListener("change", onMotionChange);
  else motionQuery.addListener(onMotionChange);
  resize();

  window.addEventListener("pagehide", stop, { once: true });
  window.addEventListener("pageshow", wake);
}

mountFaultyTerminal();
