// Local specimen behavior, independent of future product logic.
const form = document.querySelector("#specimen-form");
const input = document.querySelector("#specimen-name");
const error = document.querySelector("#name-error");
const sample = document.querySelector("#sample-text");
const size = document.querySelector("#text-size");
const dialog = document.querySelector("#sample-dialog");
const openDialog = document.querySelector("#open-dialog");
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
