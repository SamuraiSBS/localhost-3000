// Local specimen behavior, independent of future product logic.
const form = document.querySelector("#specimen-form");
const input = document.querySelector("#specimen-name");
const error = document.querySelector("#name-error");
const sample = document.querySelector("#sample-text");
const size = document.querySelector("#text-size");
const dialog = document.querySelector("#sample-dialog");
const openDialog = document.querySelector("#open-dialog");

for (const id of ["interactive-samples", "loading-sample"]) document.getElementById(id).hidden = false;
const busyToggle = document.querySelector("#busy-toggle");
const busyButton = document.querySelector("#busy-button");
busyToggle.addEventListener("change", () => {
  const busy = busyToggle.checked;
  busyButton.setAttribute("aria-busy", String(busy));
  busyButton.disabled = busy;
  const labels = busyButton.querySelectorAll(".busy-label");
  labels[0].hidden = busy; labels[1].hidden = !busy;
});
const iconToggle = document.querySelector("#icon-toggle");
iconToggle.addEventListener("click", () => {
  const selected = iconToggle.getAttribute("aria-pressed") !== "true";
  iconToggle.setAttribute("aria-pressed", String(selected));
  iconToggle.querySelector(".ds-icon").className = `ds-icon ${selected ? "ds-icon--info-solid" : "ds-icon--info"}`;
});
const tooltipWrap = document.querySelector(".ds-tooltip-wrap");
const tooltipTrigger = document.querySelector("#tooltip-trigger");
const tooltip = document.querySelector("#sample-tooltip");
tooltipWrap.addEventListener("pointerenter", () => { tooltip.hidden = false; });
tooltipWrap.addEventListener("pointerleave", () => { tooltip.hidden = true; });
tooltipTrigger.addEventListener("focus", () => { tooltip.hidden = false; });
tooltipTrigger.addEventListener("blur", () => { tooltip.hidden = true; });
// A click also exposes the explanation on touch devices.
tooltipTrigger.addEventListener("click", () => { tooltip.hidden = false; });
document.addEventListener("keydown", event => { if (event.key === "Escape") tooltip.hidden = true; });
document.querySelector("#apply-button").hidden = false;

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  const invalid = text.length === 0;
  input.setAttribute("aria-invalid", String(invalid));
  error.hidden = !invalid;
  if (invalid) {
    input.focus();
    return;
  }
  sample.textContent = text;
});

input.addEventListener("input", () => {
  if (input.value.trim()) {
    input.removeAttribute("aria-invalid");
    error.hidden = true;
  }
});

size.addEventListener("change", () => {
  sample.dataset.size = size.value;
});

if (typeof dialog.showModal === "function") {
  openDialog.hidden = false;
  openDialog.addEventListener("click", () => dialog.showModal());
  // This specimen has exactly one focusable dialog control. Keep Tab inside it.
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      event.preventDefault();
      dialog.querySelector("button").focus();
    }
  });
}
dialog.addEventListener("close", () => openDialog.focus());
