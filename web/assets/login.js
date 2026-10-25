import { api, loginDestination } from './api.js';
const form = document.querySelector('#login-form');
const error = document.querySelector('#login-error');
const submit = document.querySelector('#login-submit');
const toggle = document.querySelector('#show-password');
const oidcArea = document.querySelector('#oidc-area');
const oidcLink = document.querySelector('#oidc-login');
const oidcMessage = document.querySelector('#oidc-message');
const query = new URLSearchParams(location.search);
const demo = document.querySelector('#demo-login');
api('/auth/demo/config').then(({ data }) => {
  document.querySelector('#demo-access').hidden = !data.enabled;
}).catch(() => {});
demo.addEventListener('click', async () => {
  const notice = document.querySelector('#demo-error');
  notice.hidden = true; demo.disabled = true; submit.disabled = true;
  demo.setAttribute('aria-busy', 'true'); demo.textContent = 'Открываем демо…';
  try {
    await api('/auth/demo', { method: 'POST' });
    form.elements.password.value = '';
    location.replace(loginDestination());
  } catch (failure) {
    notice.textContent = failure.status === 429 ? 'Слишком много входов. Подождите минуту и повторите.' : failure.status === 503 ? 'Все демо-песочницы заняты. Попробуйте позже.' : 'Не удалось открыть демо. Проверьте соединение и повторите.';
    notice.hidden = false;
    demo.disabled = false; submit.disabled = false; demo.removeAttribute('aria-busy'); demo.textContent = 'Зайти на демо ↗';
  }
});
const oidcErrors = {
  disabled: 'Вход через Keycloak не настроен. Используйте логин и пароль.',
  provider_unavailable: 'Keycloak сейчас недоступен. Повторите позже или используйте логин и пароль.',
  invalid_state: 'Попытка входа или выхода устарела либо уже использована. Начните снова.',
  invalid_response: 'Не удалось подтвердить ответ Keycloak. Начните вход снова или используйте логин и пароль.',
  access_denied: 'Вход через Keycloak не разрешён для этой учётной записи.',
  logout_incomplete: 'Вы вышли из панели. Keycloak недоступен: его сессия может оставаться активной. Повторите выход в Keycloak после восстановления связи.',
};
const oidcError = query.get('oidc_error');
if (oidcErrors[oidcError]) { oidcMessage.textContent = oidcErrors[oidcError]; oidcMessage.hidden = false; }
else if (query.get('oidc_logged_out') === '1') { oidcMessage.textContent = 'Вы вышли из панели и Keycloak.'; oidcMessage.hidden = false; }
// Configuration contains no client secret, subject list, token or customer data.
api('/auth/oidc/config').then(({ data }) => {
  if (data?.enabled) { oidcLink.href = '/api/v1/auth/oidc/start?next=' + encodeURIComponent(loginDestination()); oidcArea.hidden = false; }
}).catch(() => { /* Primary login remains available if optional configuration cannot be read. */ });
toggle.addEventListener('click', () => { const input = form.elements.password; const show = input.type === 'password'; input.type = show ? 'text' : 'password'; toggle.textContent = show ? 'Скрыть пароль' : 'Показать пароль'; toggle.setAttribute('aria-pressed', String(show)); });
form.addEventListener('submit', async event => {
  event.preventDefault(); error.hidden = true; submit.disabled = true; submit.setAttribute('aria-busy', 'true'); submit.textContent = 'Входим…';
  for (const input of form.querySelectorAll('input')) input.removeAttribute('aria-invalid');
  try { await api('/auth/login', { method: 'POST', body: JSON.stringify({ login: form.elements.login.value, password: form.elements.password.value }) }); form.elements.password.value = ''; location.replace(loginDestination()); }
  catch (failure) { error.textContent = failure.status === 401 ? 'Неверный логин или пароль.' : failure.status === 429 ? 'Слишком много попыток. Подождите и повторите.' : 'Не удалось войти. Проверьте соединение и повторите.'; error.hidden = false; if (failure.status === 401) for (const input of form.querySelectorAll('input')) input.setAttribute('aria-invalid', 'true'); }
  finally { submit.disabled = false; submit.removeAttribute('aria-busy'); submit.textContent = 'Войти'; }
});
