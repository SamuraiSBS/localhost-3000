import { api, authenticate, clearSession, errorText } from './api.js';
export { api, errorText };
export const levels = { high: 'Высокий', medium: 'Средний', low: 'Низкий', unknown: 'Недостаточно данных' };
const money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'EUR' });
const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
export const eur = cents => cents === null || cents === undefined ? 'Нет денежной оценки' : money.format(cents / 100);
export const score = row => row.risk_score === null ? 'Недостаточно данных — нет возобновляемых услуг' : `${number.format(row.risk_score)} / 100 · ${levels[row.risk_level] || row.risk_level}`;
export const date = value => value ? new Date(value).toLocaleString('ru-RU', { timeZone: 'UTC' }) + ' UTC' : 'Нет даты';
export function el(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); if (className) node.className = className; return node; }
export function metadata(data) { return el('p', `Расчёт: ${date(data.evaluated_at)} · Горизонт: ${data.horizon_days} дней · Модель: ${data.model_version} · Данные: ${data.data_revision} · Настройки: ${data.settings_revision} · Расчёт: ${data.analysis_revision}`, 'metadata'); }
export function pairs(items) { const dl = el('dl'); for (const [key, value] of items) dl.append(el('dt', key), el('dd', value)); return dl; }
export function list(items) { const ul = el('ul'); for (const text of items) ul.append(el('li', text)); return ul; }
export async function startShell(active) {
  const session = await authenticate();
  document.querySelector('#user-login').textContent = session.user.login;
  document.querySelector(`[data-nav="${active}"]`)?.setAttribute('aria-current', 'page');
  const logout = document.querySelector('#logout');
  logout.addEventListener('click', async () => {
    logout.disabled = true;
    try { await api('/auth/logout', { method: 'POST' }); clearSession(); location.replace('/login'); }
    catch (error) { if (error.status !== 401) { logout.disabled = false; logout.textContent = 'Повторить выход'; const notice = el('p', errorText(error)); notice.setAttribute('role', 'alert'); logout.after(notice); } }
  });
  // A page restored from back/forward cache must revalidate before showing data.
  addEventListener('pagehide', clearSession);
  addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
  return session;
}
