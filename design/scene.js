import { PrismMaterial } from "./prism.js";
import { DitherPreview } from "./dither.js?v=20261022-2";
import { LiquidGlassMaterial } from "./liquid-glass.js?v=20261022-1";

const entry = document.querySelector("#entry");
const workspace = document.querySelector("#workspace");
const dither = new DitherPreview(document.querySelector("#scene-backdrop"), document.querySelector("#dither-status"));
const dialog = document.querySelector("#material-dialog");
const openMaterial = document.querySelector("#open-material");
const entryLink = document.querySelector(".enter-button");
const entryGlass = new LiquidGlassMaterial(entryLink, document.querySelector("#glass-status"), { radiusFromCSS: true });
let material;
let transitioning = false;
let transitionId = 0;

function duration() {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--motion-enter").trim();
  return Math.min(1000, Math.max(0, parseFloat(raw) * (raw.endsWith("ms") ? 1 : 1000))) || 0;
}

async function animate(element, frames, milliseconds) {
  if (!milliseconds || !element.animate || document.hidden) return;
  const animation = element.animate(frames, { duration: milliseconds, easing: "cubic-bezier(0.16,1,0.3,1)" });
  // Animation events and hidden tabs must never block navigation.
  await Promise.race([animation.finished.catch(() => {}), new Promise(resolve => setTimeout(resolve, milliseconds + 80))]);
  animation.cancel();
}

async function show(view, withMotion = false, moveFocus = true) {
  const id = ++transitionId;
  for (const section of [entry, workspace]) section.getAnimations().forEach(animation => animation.cancel());
  transitioning = true;
  if (dialog.open) dialog.close();
  const target = view === "workspace" ? workspace : entry;
  const previous = view === "workspace" ? entry : workspace;
  const ms = withMotion ? duration() : 0;
  entryLink.setAttribute("aria-disabled", "true");
  await animate(previous, [{ opacity: 1 }, { opacity: 0 }], ms * 0.4);
  if (id !== transitionId) return;
  previous.hidden = true;
  target.hidden = false;
  window.scrollTo(0, 0);
  if (moveFocus) target.querySelector("h1,h2").focus({ preventScroll: true });
  await animate(target, [{ opacity: 0 }, { opacity: 1 }], ms * 0.6);
  if (id !== transitionId) return;
  entryLink.removeAttribute("aria-disabled");
  transitioning = false;
}

for (const link of document.querySelectorAll('a[href="#workspace"], a[href="#entry"]')) {
  link.addEventListener("click", event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (transitioning) return;
    const view = link.hash === "#workspace" ? "workspace" : "entry";
    history.pushState(null, "", link.hash);
    show(view, !link.classList.contains("ds-skip"));
  });
}
window.addEventListener("popstate", () => show(location.hash === "#workspace" ? "workspace" : "entry"));
show(location.hash === "#workspace" ? "workspace" : "entry", false, false);
window.addEventListener("pagehide", event => { if (!event.persisted) { dither.destroy(); entryGlass.destroy(); } });

if (typeof dialog.showModal === "function") {
  openMaterial.hidden = false;
  openMaterial.addEventListener("click", () => {
    dialog.showModal();
    material ??= new PrismMaterial(document.querySelector("#prism-stage"));
    material.render();
  });
  dialog.addEventListener("close", () => {
    if (!workspace.hidden) openMaterial.focus({ preventScroll: true });
  });
  dialog.addEventListener("keydown", event => {
    if (event.key !== "Tab") return;
    const controls = [...dialog.querySelectorAll("button,input")].filter(element => !element.disabled && element.getClientRects().length);
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  });
}
