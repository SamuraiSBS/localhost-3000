import {
  renderLoadError,
  renderLoading,
  renderProfilePage,
  renderProfileSummary,
  setFieldError,
  showToast,
} from "./components.js";

document.documentElement.classList.add("has-js");

const content = document.querySelector("#profile-content");
const toast = document.querySelector("#profile-toast");
const announcer = document.querySelector("#profile-announcer");
let profile = null;
let toastTimer = 0;

function showSuccess(message) {
  window.clearTimeout(toastTimer);
  showToast(toast, message);
  toastTimer = window.setTimeout(() => {
    toast.hidden = true;
    toast.replaceChildren();
  }, 6000);
}

function validationMessage(field, form) {
  const value = field.value;
  if (field.required && (field.type === "text" || field.type === "email" ? value.trim() === "" : value === "")) {
    if (field.id === "profile-name") return "Введите имя.";
    if (field.id === "profile-email") return "Введите электронную почту.";
    if (field.id === "current-password") return "Введите текущий пароль.";
    if (field.id === "new-password") return "Введите новый пароль.";
    if (field.id === "confirm-password") return "Повторите новый пароль.";
  }
  if (field.type === "email" && field.validity.typeMismatch) {
    return "Введите адрес в формате name@example.com.";
  }
  if (field.id === "confirm-password") {
    const newPassword = form.querySelector("#new-password");
    if (value !== newPassword.value) return "Пароли не совпадают. Проверьте оба поля.";
  }
  return "";
}

function connectFormValidation(form, fields) {
  let submitted = false;
  const touched = new Set();

  for (const field of fields) {
    field.addEventListener("blur", () => {
      touched.add(field);
      setFieldError(field, validationMessage(field, form));
    });
    field.addEventListener("input", () => {
      if (submitted || touched.has(field)) setFieldError(field, validationMessage(field, form));
      if (field.id === "new-password") {
        const confirmation = form.querySelector("#confirm-password");
        if (confirmation && (submitted || touched.has(confirmation))) {
          setFieldError(confirmation, validationMessage(confirmation, form));
        }
      }
    });
  }

  form.addEventListener("submit", () => { submitted = true; });

  return {
    validate() {
      const invalidFields = fields.filter((field) => !setFieldError(field, validationMessage(field, form)));
      if (invalidFields.length) invalidFields[0].focus();
      return invalidFields.length === 0;
    },
    reset() {
      submitted = false;
      touched.clear();
    },
  };
}

function connectPersonalForm() {
  const form = content.querySelector("#personal-data-form");
  const fields = [...form.querySelectorAll("input[required]")];
  const validation = connectFormValidation(form, fields);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!validation.validate()) return;

    profile = {
      ...profile,
      name: form.elements.name.value.trim(),
      email: form.elements.email.value.trim(),
    };
    const summary = content.querySelector(".profile-summary");
    summary.outerHTML = renderProfileSummary(profile);
    form.elements.name.value = profile.name;
    form.elements.email.value = profile.email;
    showSuccess("Личные данные обновлены в этом просмотре.");
  });
}

function connectPasswordForm() {
  const form = content.querySelector("#password-form");
  const fields = [...form.querySelectorAll("input[required]")];
  const validation = connectFormValidation(form, fields);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!validation.validate()) return;

    for (const field of fields) {
      field.value = "";
      setFieldError(field, "");
    }
    validation.reset();
    showSuccess("Проверка пройдена. Пароль не отправлен и не сохраняется.");
  });
}

async function loadProfile(restoreFocus = false) {
  content.setAttribute("aria-busy", "true");
  content.innerHTML = renderLoading();
  announcer.textContent = "";

  try {
    const response = await fetch("./demo-profile.json", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("Demo profile request failed");
    const data = await response.json();
    if (typeof data?.name !== "string" || typeof data?.email !== "string") {
      throw new Error("Demo profile has an unsupported shape");
    }

    profile = { name: data.name, email: data.email };
    content.innerHTML = renderProfilePage(profile);
    content.setAttribute("aria-busy", "false");
    connectPersonalForm();
    connectPasswordForm();
    announcer.textContent = "Демонстрационный профиль загружен.";
    if (restoreFocus) content.focus({ preventScroll: true });
  } catch {
    content.innerHTML = renderLoadError();
    content.setAttribute("aria-busy", "false");
    announcer.textContent = "Не удалось загрузить демонстрационный профиль.";
    if (restoreFocus) content.querySelector("[data-profile-retry]")?.focus();
  }
}

content.addEventListener("click", (event) => {
  if (event.target.closest("[data-profile-retry]")) loadProfile(true);
});

loadProfile();
