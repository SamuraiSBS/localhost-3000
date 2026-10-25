import { play, milliseconds } from './motion.js';

const art = document.querySelector('.ruins__art');
const image = art?.querySelector('img');
const title = document.querySelector('.ruins__title');
const action = document.querySelector('.ruins__enter');
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
let frame;
let canvas;

function finish() {
  cancelAnimationFrame(frame);
  canvas?.remove();
  if (image) image.style.visibility = '';
}

async function entrance() {
  if (!image || reduced.matches || document.hidden || matchMedia('(forced-colors: active)').matches) return;
  try { await image.decode(); } catch { return; }
  if (reduced.matches || document.hidden) return;
  // Draw the original art under a diminishing Bayer veil. No asset is rewritten,
  // and no continuous animation loop remains after the entrance.
  canvas = document.createElement('canvas');
  canvas.className = 'ruins__reveal'; canvas.setAttribute('aria-hidden', 'true');
  canvas.width = Math.min(768, image.naturalWidth); canvas.height = Math.round(canvas.width * image.naturalHeight / image.naturalWidth);
  const ctx = canvas.getContext('2d', { alpha: false });
  if (!ctx) return;
  const tile = document.createElement('canvas'); tile.width = 8; tile.height = 8;
  const tileCtx = tile.getContext('2d');
  if (!tileCtx) return;
  const bayer = [0,32,8,40,2,34,10,42,48,16,56,24,50,18,58,26,12,44,4,36,14,46,6,38,60,28,52,20,62,30,54,22,3,35,11,43,1,33,9,41,51,19,59,27,49,17,57,25,15,47,7,39,13,45,5,37,63,31,55,23,61,29,53,21];
  const background = getComputedStyle(document.documentElement).getPropertyValue('--color-bg').trim();
  const start = performance.now();
  function draw(now) {
    const progress = Math.min(1, (now - start) / Math.max(1,milliseconds('--motion-enter',800)));
    ctx.fillStyle = background; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    tileCtx.clearRect(0, 0, 8, 8); tileCtx.fillStyle = background;
    const threshold = (1 - Math.pow(1 - progress, 3)) * 64;
    for (let i = 0; i < 64; i++) if (bayer[i] >= threshold) tileCtx.fillRect(i % 8, Math.floor(i / 8), 1, 1);
    ctx.fillStyle = ctx.createPattern(tile, 'repeat'); ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (progress < 1 && !document.hidden && !reduced.matches) frame = requestAnimationFrame(draw);
    else finish();
  }
  draw(start); art.append(canvas); image.style.visibility = 'hidden';
  const blur=getComputedStyle(document.documentElement).getPropertyValue('--motion-blur').trim();
  const duration=milliseconds('--motion-enter',800);
  play(title, [{ opacity: 0, filter: `blur(${blur})` }, { opacity: 1, filter: 'blur(0px)' }], { duration: duration*.75, delay: duration*.3, fill: 'backwards' });
  play(action, [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }], { duration: duration*.6, delay: duration*.5, fill: 'backwards' });
}
entrance().catch(finish);
reduced.addEventListener('change', finish);
document.addEventListener('visibilitychange', () => { if (document.hidden) finish(); });
addEventListener('pagehide', finish);
