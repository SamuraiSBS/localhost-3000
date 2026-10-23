import { DitherPreview } from "./dither.js";

const stage = document.querySelector("#dither-stage");
const form = document.querySelector("#dither-controls");
const grid = document.querySelector("#dither-size");
const threshold = document.querySelector("#dither-threshold");
const thresholdValue = document.querySelector("#threshold-value");
const defaults = getComputedStyle(stage);
const initialGrid = defaults.getPropertyValue("--dither-grid-size").trim();
const initialThreshold = defaults.getPropertyValue("--dither-threshold").trim();
grid.value = initialGrid;
threshold.value = initialThreshold;
thresholdValue.value = Number(threshold.value).toFixed(2);

const preview = new DitherPreview(stage, document.querySelector("#dither-fallback"), ready => { form.hidden = !ready; });
function update() {
  stage.style.setProperty("--dither-grid-size", grid.value);
  stage.style.setProperty("--dither-threshold", threshold.value);
  thresholdValue.value = Number(threshold.value).toFixed(2);
  preview.render();
}
grid.addEventListener("change", update);
threshold.addEventListener("input", update);
form.addEventListener("submit", event => event.preventDefault());
document.querySelector("#dither-reset").addEventListener("click", () => {
  grid.value = initialGrid;
  threshold.value = initialThreshold;
  update();
});
window.addEventListener("pagehide", event => { if (!event.persisted) preview.destroy(); });
