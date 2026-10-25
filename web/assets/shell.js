import { api, authenticate, clearSession, errorText } from './api.js';
import { play } from '/design/motion.js';
export { api, errorText };
export const levels = { high: 'Высокий', medium: 'Средний', low: 'Низкий', unknown: 'Недостаточно данных' };
const money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'EUR' });
const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
export const eur = cents => cents === null || cents === undefined ? 'Нет денежной оценки' : money.format(cents / 100);
export const score = row => row.risk_score === null ? 'Недостаточно данных — нет возобновляемых услуг' : `${number.format(row.risk_score)} / 100 · ${levels[row.risk_level] || row.risk_level}`;
export const date = value => value ? new Date(value).toLocaleString('ru-RU', { timeZone: 'UTC' }) + ' UTC' : 'Нет даты';
export function el(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); if (className) node.className = className; return node; }
export function metadata(data) {
  const details = el('details', undefined, 'metadata');
  details.dataset.calcMeta = 'revision';
  details.append(el('summary', `Расчёт: ${date(data.evaluated_at)} · ${data.horizon_days} дней`),
    el('p', `Модель: ${data.model_version} · Данные: ${data.data_revision} · Настройки: ${data.settings_revision} · Расчёт: ${data.analysis_revision}`));
  return details;
}
export function pairs(items) { const dl = el('dl'); for (const [key, value] of items) dl.append(el('dt', key), el('dd', value)); return dl; }
export function list(items) { const ul = el('ul'); for (const text of items) ul.append(el('li', text)); return ul; }
export async function startShell(active) {
  const session = await authenticate();
  document.querySelector('#user-login').textContent = session.user.login;
  if (session.user.id === 'demo') {
    const banner = el('aside', undefined, 'demo-banner');
    banner.setAttribute('aria-label', 'Демонстрационный аккаунт');
    const text = el('p');
    text.append(el('strong', 'Демо-песочница. '), 'Учебные данные и изменения видны только в этой сессии. После нового входа демо начнётся заново.');
    banner.append(text); document.querySelector('header').after(banner);
  }
  const navigation = document.querySelector('#product-navigation');
  if (!navigation.querySelector('[data-nav="lab"]')) {
    const labLink = el('a', 'Лаборатория'); labLink.href = '/lab'; labLink.dataset.nav = 'lab'; navigation.append(labLink);
  }
  document.querySelector(`[data-nav="${active}"]`)?.setAttribute('aria-current', 'page');
  const nav = document.querySelector('#product-navigation');
  const sessionArea = document.querySelector('.session');
  const headerInner = nav.parentElement;
  const narrow = matchMedia('(max-width: 44rem)');
  const toggle = el('button', 'Меню', 'ds-button ds-button--secondary menu-toggle');
  toggle.type = 'button'; toggle.setAttribute('aria-controls', nav.id); toggle.setAttribute('aria-expanded', 'false');
  nav.before(toggle);
  function closeMenu(returnFocus = false) {
    if (narrow.matches) nav.append(sessionArea);
    else headerInner.append(sessionArea);
    nav.hidden = narrow.matches;
    toggle.setAttribute('aria-expanded', 'false');
    if (returnFocus) toggle.focus();
  }
  toggle.addEventListener('click', () => {
    nav.hidden = !nav.hidden; toggle.setAttribute('aria-expanded', String(!nav.hidden));
    if (!nav.hidden) play(nav, [{opacity: 0, transform: 'translateY(-6px)'}, {opacity: 1, transform: 'none'}], {duration: 200});
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && narrow.matches && !nav.hidden) closeMenu(true); });
  document.addEventListener('click', event => { if (narrow.matches && !nav.hidden && !nav.contains(event.target) && !toggle.contains(event.target)) closeMenu(); });
  narrow.addEventListener('change', () => closeMenu()); closeMenu();
  const logout = document.querySelector('#logout');
  logout.addEventListener('click', async () => {
    logout.disabled = true;
    try { const result = await api('/auth/logout', { method: 'POST' }); clearSession(); location.replace(result.logoutUrl || '/login'); }
    catch (error) { if (error.status !== 401) { logout.disabled = false; logout.textContent = 'Повторить выход'; const notice = el('p', errorText(error)); notice.setAttribute('role', 'alert'); logout.after(notice); } }
  });
  // A page restored from back/forward cache must revalidate before showing data.
  addEventListener('pagehide', clearSession);
  addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
  return session;
}
