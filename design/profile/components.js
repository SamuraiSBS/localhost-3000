const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");

export function getInitials(name) {
  const parts = String(name).trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  const first = parts[0].slice(0, 1);
  const last = parts.length > 1 ? parts.at(-1).slice(0, 1) : "";
  return `${first}${last}`.toLocaleUpperCase("ru-RU");
}

export function renderAvatar(name) {
  return `<div class="profile-avatar" aria-hidden="true">${escapeHtml(getInitials(name))}</div>`;
}

export function renderField({ id, name, label, type = "text", value = "", autocomplete }) {
  const valueAttribute = type === "password" ? "" : ` value="${escapeHtml(value)}"`;
  const autocompleteAttribute = autocomplete ? ` autocomplete="${escapeHtml(autocomplete)}"` : "";
  return `
    <div class="ds-field profile-field">
      <label class="ds-label" for="${escapeHtml(id)}">${escapeHtml(label)}</label>
      <input class="ds-input" id="${escapeHtml(id)}" name="${escapeHtml(name)}" type="${escapeHtml(type)}"${valueAttribute}${autocompleteAttribute} aria-describedby="${escapeHtml(id)}-error" required>
      <p class="ds-error profile-field__error" id="${escapeHtml(id)}-error" aria-live="polite" aria-atomic="true" hidden>
        <span class="profile-error__mark" aria-hidden="true">!</span>
        <span data-error-message></span>
      </p>
    </div>`;
}

function renderSection({ id, title, description, formId, fields, buttonLabel }) {
  return `
    <section class="profile-section" aria-labelledby="${escapeHtml(id)}">
      <div class="profile-section__heading">
        <h2 id="${escapeHtml(id)}">${escapeHtml(title)}</h2>
        <p>${escapeHtml(description)}</p>
      </div>
      <form class="profile-form" id="${escapeHtml(formId)}" novalidate>
        <div class="profile-form__fields">${fields.map(renderField).join("")}</div>
        <div class="profile-actions">
          <button class="ds-button" type="submit">${escapeHtml(buttonLabel)}</button>
        </div>
      </form>
    </section>`;
}

export function renderProfileSummary(profile) {
  return `
    <section class="profile-summary" aria-label="Пользователь профиля">
      ${renderAvatar(profile.name)}
      <div class="profile-identity">
        <h2>${escapeHtml(profile.name)}</h2>
        <p>${escapeHtml(profile.email)}</p>
      </div>
    </section>`;
}

export function renderProfilePage(profile) {
  const personalFields = [
    { id: "profile-name", name: "name", label: "Имя", value: profile.name, autocomplete: "name" },
    { id: "profile-email", name: "email", label: "Электронная почта", type: "email", value: profile.email, autocomplete: "email" },
  ];
  const passwordFields = [
    { id: "current-password", name: "currentPassword", label: "Текущий пароль", type: "password", autocomplete: "current-password" },
    { id: "new-password", name: "newPassword", label: "Новый пароль", type: "password", autocomplete: "new-password" },
    { id: "confirm-password", name: "confirmPassword", label: "Повторите новый пароль", type: "password", autocomplete: "new-password" },
  ];

  return `
    ${renderProfileSummary(profile)}
    <div class="profile-grid">
      ${renderSection({
        id: "personal-data-title",
        title: "Личные данные",
        description: "Имя и почта отображаются в профиле.",
        formId: "personal-data-form",
        fields: personalFields,
        buttonLabel: "Сохранить изменения",
      })}
      ${renderSection({
        id: "password-title",
        title: "Смена пароля",
        description: "В демо-режиме пароль не отправляется и не сохраняется.",
        formId: "password-form",
        fields: passwordFields,
        buttonLabel: "Проверить форму",
      })}
    </div>`;
}

export function renderLoading() {
  return '<p class="profile-feedback" role="status">Загружаем демонстрационный профиль…</p>';
}

export function renderLoadError() {
  return `
    <section class="profile-load-error" role="alert" aria-labelledby="profile-load-error-title">
      <h2 id="profile-load-error-title">Не удалось загрузить профиль</h2>
      <p>Проверьте локальный сервер и повторите попытку.</p>
      <button class="ds-button ds-button--secondary" type="button" data-profile-retry>Повторить</button>
    </section>`;
}

export function setFieldError(field, message) {
  const error = document.getElementById(field.getAttribute("aria-describedby"));
  const errorText = error?.querySelector("[data-error-message]");
  if (errorText) errorText.textContent = message;
  if (error) error.hidden = !message;
  if (message) field.setAttribute("aria-invalid", "true");
  else field.removeAttribute("aria-invalid");
  return message === "";
}

export function showToast(toast, message) {
  const icon = document.createElement("span");
  icon.className = "profile-toast__icon";
  icon.setAttribute("aria-hidden", "true");
  const text = document.createElement("span");
  text.textContent = message;
  toast.replaceChildren(icon, text);
  toast.hidden = false;
}
