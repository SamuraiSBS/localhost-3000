// Same-origin API. CSRF stays in memory and is bound to the server session.
let csrf = null;
export class ApiError extends Error {
  constructor(status, payload) { super(payload?.error?.message || 'Запрос не выполнен'); this.status = status; this.payload = payload; }
}
export function clearSession() {
  csrf = null;
  document.querySelectorAll('#customer-detail, #summary, #churn-chart, #customers-table tbody, #feature-fields').forEach(node => node.replaceChildren());
  document.querySelectorAll('input').forEach(node => { node.value = ''; });
}
export function loginDestination() {
  const next = new URLSearchParams(location.search).get('next') || '';
  return /^\/customers(?:\/[1-9][0-9]*)?$/.test(next) || next === '/settings' ? next : '/customers';
}
export async function api(path, options = {}) {
  const headers = new Headers(options.headers);
  if (options.body !== undefined) headers.set('Content-Type', 'application/json');
  if (csrf && options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf);
  let response;
  try { response = await fetch('/api/v1' + path, { ...options, headers, credentials: 'same-origin', cache: 'no-store' }); }
  catch { throw new ApiError(0, { error: { message: 'Нет связи с сервером. Проверьте соединение и повторите.' } }); }
  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') {
      clearSession();
      location.replace('/login?next=' + encodeURIComponent(location.pathname));
    }
    throw new ApiError(response.status, payload);
  }
  if (payload?.csrf_token) csrf = payload.csrf_token;
  return { data: payload, etag: response.headers.get('ETag') };
}
export async function authenticate() { return (await api('/auth/me')).data; }
export function errorText(error) {
  if (error.status === 0) return error.message;
  if (error.status === 401) return 'Сессия завершена. Войдите снова.';
  if (error.status === 403) return 'Действие не разрешено. Обновите страницу и повторите.';
  if (error.status >= 500) return 'Сервер не смог выполнить запрос. Повторите загрузку.';
  return 'Запрос не выполнен. Проверьте данные и повторите.';
}
