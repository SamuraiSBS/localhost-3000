import { DitherPreview } from "./dither.js?v=20261022-2";
import { LiquidGlassMaterial } from "./liquid-glass.js?v=20261022-1";

const dither = new DitherPreview(
  document.querySelector("#scene-backdrop"),
  document.querySelector("#dither-status"),
);
const glassStatus = document.querySelector("#glass-status");
const glassSurfaces = [
  ...document.querySelectorAll(".auth__submit"),
  ...document.querySelectorAll(".auth-password__toggle"),
];
const glassMaterials = glassSurfaces.map(surface => new LiquidGlassMaterial(
  surface,
  surface.matches(".auth__submit") ? glassStatus : null,
  { radiusFromCSS: true },
));

document.querySelectorAll(".auth-form").forEach(form => {
  form.noValidate = true;

  const fields = [...form.querySelectorAll("input[required]")];
  const email = form.querySelector('input[type="email"]');
  const password = form.querySelector("#password");
  const confirmation = form.querySelector("#password-confirm");
  const status = form.parentElement.querySelector(".auth__form-status");
  const shownErrors = new Set();
  const errorElements = new Map(fields.map(field => [
    field,
    document.getElementById(field.getAttribute("aria-describedby")),
  ]));
  let submitAttempted = false;

  function messageFor(field) {
    const empty = field.type === "email" ? field.value.trim() === "" : field.value === "";
    if (field.required && empty) {
      if (field.type === "email") return "Введите электронную почту.";
      if (field === confirmation) return "Повторите пароль.";
      return "Введите пароль.";
    }
    if (field === email && field.validity.typeMismatch) {
      return "Введите адрес электронной почты в формате name@example.com.";
    }
    if (field === confirmation && field.value !== password.value) {
      return "Пароли не совпадают. Проверьте оба поля.";
    }
    return "";
  }

  function renderError(field, visible) {
    const error = errorElements.get(field);
    const message = messageFor(field);
    if (!visible) {
      field.removeAttribute("aria-invalid");
      error.hidden = true;
      error.textContent = "";
      return message === "";
    }
    if (message) {
      field.setAttribute("aria-invalid", "true");
      error.textContent = message;
      error.hidden = false;
    } else {
      field.removeAttribute("aria-invalid");
      error.textContent = "";
      error.hidden = true;
    }
    return message === "";
  }

  fields.forEach(field => {
    field.addEventListener("blur", () => {
      shownErrors.add(field);
      renderError(field, true);
    });
    field.addEventListener("input", () => {
      status.hidden = true;
      if (submitAttempted || shownErrors.has(field)) renderError(field, true);
      if (field === password && confirmation && (submitAttempted || shownErrors.has(confirmation))) {
        renderError(confirmation, true);
      }
    });
  });

  form.addEventListener("submit", event => {
    event.preventDefault();
    submitAttempted = true;
    const invalidFields = fields.filter(field => !renderError(field, true));
    if (invalidFields.length > 0) {
      status.hidden = true;
      invalidFields[0].focus();
      return;
    }
    status.hidden = false;
  });
});

document.querySelectorAll(".auth-password__toggle").forEach(button => {
  const input = document.getElementById(button.getAttribute("aria-controls"));
  const [eye, eyeOff] = button.querySelectorAll("svg");
  button.addEventListener("click", () => {
    const showPassword = input.type === "password";
    input.type = showPassword ? "text" : "password";
    button.setAttribute("aria-label", showPassword ? "Скрыть пароль" : "Показать пароль");
    eye.hidden = showPassword;
    eyeOff.hidden = !showPassword;
  });
  button.hidden = false;
});

window.addEventListener("pagehide", () => {
  dither.destroy();
  glassMaterials.forEach(material => material.destroy());
}, { once: true });
