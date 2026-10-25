/* Shared native motion. Content is visible before JS and after every failure. */
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const running = new Set();
const seen = new WeakSet();
const ease = 'cubic-bezier(0.16, 1, 0.3, 1)';
export function milliseconds(token, fallback = 0) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  return Number.isFinite(parseFloat(value)) ? parseFloat(value) * (value.endsWith('ms') ? 1 : 1000) : fallback;
}

export function play(node, frames, options = {}) {
  if (!node?.animate || reduced.matches || document.hidden) return;
  const animation = node.animate(frames, { duration: milliseconds('--motion-reveal',560), easing: ease, ...options });
  running.add(animation);
  animation.finished.catch(() => {}).finally(() => running.delete(animation));
  return animation;
}

export function reveal(nodes, { delay = 0, stagger = milliseconds('--motion-stagger',45) } = {}) {
  [...nodes].forEach((node, index) => {
    if (seen.has(node)) return;
    seen.add(node);
    play(node, [{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }],
      { duration: milliseconds('--motion-reveal',560), delay: delay + Math.min(index, 5) * stagger, fill: 'backwards' });
  });
}

const candidates = '.main > h1, .page-heading, .summary-line > p, .section, .service, .feature-field, .login-area, .auth__form-area, .error-page__content';
const observed = new WeakSet();
const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
  const visible = entries.filter(entry => entry.isIntersecting).map(entry => entry.target);
  reveal(visible);
  visible.forEach(node => observer.unobserve(node));
}, { threshold: 0.06 }) : null;

function discover(root) {
  const nodes = [...(root.matches?.(candidates) ? [root] : []), ...root.querySelectorAll(candidates)];
  for (const node of nodes) {
    if (observed.has(node)) continue;
    observed.add(node);
    if (observer) observer.observe(node);
    else reveal([node]);
  }
}

discover(document);
const mutations = new MutationObserver(records => {
  for (const record of records) for (const node of record.addedNodes) {
    if (node.nodeType === Node.ELEMENT_NODE) discover(node);
  }
});
mutations.observe(document.body, { childList: true, subtree: true });

// Native details preserve keyboard behavior and remain usable without JS.
document.addEventListener('toggle', event => {
  if (event.target.matches('details') && event.target.open) {
    reveal([...event.target.children].filter(node => node.tagName !== 'SUMMARY'), { stagger: 25 });
  }
}, true);

function stop() { for (const animation of running) animation.cancel(); running.clear(); }
reduced.addEventListener('change', event => { if (event.matches) stop(); });
document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
addEventListener('pagehide', () => { stop(); observer?.disconnect(); mutations.disconnect(); });
