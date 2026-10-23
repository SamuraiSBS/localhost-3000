import { DitherPreview } from '/design/dither.js';
import { api, loginDestination } from './api.js';
const form = document.querySelector('#login-form');
const error = document.querySelector('#login-error');
const submit = document.querySelector('#login-submit');
const toggle = document.querySelector('#show-password');
toggle.addEventListener('click', () => { const input = form.elements.password; const show = input.type === 'password'; input.type = show ? 'text' : 'password'; toggle.textContent = show ? 'Скрыть пароль' : 'Показать пароль'; toggle.setAttribute('aria-pressed', String(show)); });
form.addEventListener('submit', async event => {
  event.preventDefault(); error.hidden = true; submit.disabled = true; submit.setAttribute('aria-busy', 'true'); submit.textContent = 'Входим…';
  for (const input of form.querySelectorAll('input')) input.removeAttribute('aria-invalid');
  try { await api('/auth/login', { method: 'POST', body: JSON.stringify({ login: form.elements.login.value, password: form.elements.password.value }) }); form.elements.password.value = ''; location.replace(loginDestination()); }
  catch (failure) { error.textContent = failure.status === 401 ? 'Неверный логин или пароль.' : failure.status === 429 ? 'Слишком много попыток. Подождите и повторите.' : 'Не удалось войти. Проверьте соединение и повторите.'; error.hidden = false; if (failure.status === 401) for (const input of form.querySelectorAll('input')) input.setAttribute('aria-invalid', 'true'); }
  finally { submit.disabled = false; submit.removeAttribute('aria-busy'); submit.textContent = 'Войти'; }
});

new DitherPreview(document.querySelector('#scene-backdrop'),document.querySelector('#dither-status'));
