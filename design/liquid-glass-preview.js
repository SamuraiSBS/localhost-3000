import { LiquidGlassMaterial } from "./liquid-glass.js";
import { readLiquidGlassPreset } from "./liquid-glass-preset.js";

const defaults = readLiquidGlassPreset();
const form = document.querySelector("#glass-controls");
const material = new LiquidGlassMaterial(document.querySelector("#liquid-lens"), document.querySelector("#glass-fallback"));
const heroMaterials = [...document.querySelectorAll(".hero-glass")].map(surface =>
  new LiquidGlassMaterial(surface, document.createElement("p"), { radiusFromCSS: true }));
// Each surface handles its own fallback; the visible note describes their shared policy.
document.querySelector("#hero-fallback").textContent = "Верхняя панель, плашка и вторичное действие используют один пресет. Основная кнопка остаётся светлой.";
const fields = [...form.querySelectorAll("[name]")];
function display(settings) {
  for (const field of fields) {
    field.value = settings[field.name];
    const output = form.querySelector(`output[for="${field.id}"]`);
    if (output) output.value = field.value;
  }
}
display(defaults);
form.addEventListener("input", () => {
  const settings = Object.fromEntries(fields.map(field => [field.name, field.name === "shape" ? field.value : Number(field.value)]));
  display(settings);
  material.update(settings);
  for (const heroMaterial of heroMaterials) heroMaterial.update(settings);
});
form.addEventListener("submit", event => event.preventDefault());
document.querySelector("#glass-reset").addEventListener("click", () => {
  display(defaults); material.update(defaults);
  for (const heroMaterial of heroMaterials) heroMaterial.update(defaults);
});
form.hidden = false;
document.querySelector("#glass-actions").hidden = false;
window.addEventListener("pagehide", event => {
  if (!event.persisted) { material.destroy(); for (const heroMaterial of heroMaterials) heroMaterial.destroy(); }
});
