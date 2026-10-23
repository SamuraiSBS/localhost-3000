import { startShell, api, el, eur, score, metadata, errorText } from './shell.js';
const summary = document.querySelector('#summary');
const region = document.querySelector('#rating-region');
const body = document.querySelector('tbody');
const status = document.querySelector('#list-status');
const retry = document.querySelector('#retry');
const form = document.querySelector('#filters');
const sortBy = document.querySelector('#sort-by');
const direction = document.querySelector('#sort-order');
const previous = document.querySelector('#previous');
const next = document.querySelector('#next');
const allowed = [...sortBy.options].map(option => option.value);
let params = new URLSearchParams(location.search);
let offset = Math.max(0, Number(params.get('offset')) || 0);
let order = params.get('sort_order') === 'asc' ? 'asc' : 'desc';
sortBy.value = allowed.includes(params.get('sort_by')) ? params.get('sort_by') : 'risk_score';
form.elements.risk_level.value = ['high','medium','low','unknown'].includes(params.get('risk_level')) ? params.get('risk_level') : '';
// Search text is never stored in the URL, where it could expose a personal email.
let query = '';
let requestId = 0;
// Controls may contain a pending request; only a matching successful response
// confirms their ordering and URL for the rows currently on screen.
let confirmed = null;
function restoreConfirmed() {
  if (!confirmed) return;
  sortBy.value = confirmed.sortBy;
  order = confirmed.order;
  offset = confirmed.offset;
  query = confirmed.query;
  form.elements.q.value = confirmed.query;
  form.elements.risk_level.value = confirmed.riskLevel;
  sortState();
  urlState();
  previous.disabled = confirmed.previousDisabled;
  next.disabled = confirmed.nextDisabled;
}
function sortState() {
  direction.textContent = order === 'asc' ? 'По возрастанию ↑' : 'По убыванию ↓';
  for (const th of document.querySelectorAll('[data-sort]')) {
    if (th.dataset.sort === sortBy.value) th.setAttribute('aria-sort', order === 'asc' ? 'ascending' : 'descending');
    else th.removeAttribute('aria-sort');
  }
}
function urlState() {
  params = new URLSearchParams({ sort_by: sortBy.value, sort_order: order });
  if (form.elements.risk_level.value) params.set('risk_level', form.elements.risk_level.value);
  if (offset) params.set('offset', String(offset));
  history.replaceState(null, '', '/customers?' + params);
}
function showSummary(data) {
  summary.replaceChildren();
  const line = el('div', undefined, 'summary-line');
  const high = el('p', 'Высокий риск');
  const highLink = el('a', data.risk_counts.high); highLink.href = '/customers?risk_level=high'; highLink.setAttribute('aria-label', `${data.risk_counts.high} клиентов высокого риска`); high.append(el('br'), highLink);
  const money = el('p', 'Стоимость продлений под риском'); money.append(el('strong', data.customers_evaluated === 0 ? 'Нет денежной оценки' : eur(data.monetary_risk_cents)));
  const unknown = el('p', 'Без оценки'); unknown.append(el('strong', data.risk_counts.unknown)); line.append(high,money,unknown);
  summary.append(line, el('p', `Одно ближайшее продление каждой услуги в горизонте ${data.horizon_days} дней; взвешено индексом риска. Сценарная оценка в EUR, не подтверждённый прогноз потерь.`), el('p', `Всего клиентов: ${data.customers_total}. Средний риск: ${data.risk_counts.medium}; низкий: ${data.risk_counts.low}. Цена ближайших продлений: ${eur(data.due_services_price_cents)}. Нехватка средств: ${eur(data.funding_gap_cents)}.`), metadata(data));
  if (data.customers_total === 0) summary.append(el('p', 'Данные ещё не загружены. Активный снимок пуст. Нужен полный JSON-снимок источника.'));
  if (data.data_quality_warnings) summary.append(el('p', `Предупреждений о качестве данных: ${data.data_quality_warnings}. Подробности в карточках клиентов.`));
  summary.setAttribute('aria-busy', 'false');
}
export async function loadCustomers() {
  const current = ++requestId;
  const requested = { sortBy: sortBy.value, order, offset, query, riskLevel: form.elements.risk_level.value };
  region.setAttribute('aria-busy','true'); summary.setAttribute('aria-busy','true');
  status.textContent = confirmed ? 'Обновляем клиентов… Показан предыдущий результат; запрошенные сортировка и фильтры ещё не применены.' : 'Загружаем клиентов…';
  retry.hidden = true;
  previous.disabled = true; next.disabled = true;
  const request = new URLSearchParams({ sort_by: requested.sortBy, sort_order: requested.order, limit: '50', offset: String(requested.offset) });
  if (requested.query) request.set('q', requested.query);
  if (requested.riskLevel) request.set('risk_level', requested.riskLevel);
  try {
    // One retry avoids combining evaluations if a write lands between the requests.
    let rows, totals;
    for (let attempt=0; attempt<2; attempt++) {
      [rows, totals] = await Promise.all([api('/customers?' + request), api('/analytics/summary')]);
      if (rows.data.analysis_revision === totals.data.analysis_revision) break;
      if (attempt === 1) throw new Error('revision_mismatch');
    }
    if (current !== requestId) return;
    showSummary(totals.data); body.replaceChildren();
    for (const row of rows.data.items) {
      const tr = el('tr');
      for (const value of [row.id, row.email]) { const td=el('td'); const link=el('a',value); link.href='/customers/'+encodeURIComponent(row.id); td.append(link); tr.append(td); }
      for (const value of [eur(row.balance_cents), eur(row.total_replenished_cents), score(row), row.top_reasons.join(' · ') || (row.risk_score === null ? 'Нет возобновляемых услуг' : 'Нет активных признаков риска'), eur(row.monetary_risk_cents)]) tr.append(el('td',value));
      body.append(tr);
    }
    status.textContent = rows.data.total === 0 ? (totals.data.customers_total === 0 ? 'Данные ещё не загружены.' : 'Нет клиентов по выбранным условиям. Сбросьте фильтр.') : `Показано ${requested.offset+1}–${requested.offset+rows.data.items.length} из ${rows.data.total} клиентов. Связь с сервером установлена.`;
    previous.disabled = requested.offset === 0; next.disabled = requested.offset + rows.data.items.length >= rows.data.total;
    confirmed = { ...requested, evaluatedAt: totals.data.evaluated_at, previousDisabled: previous.disabled, nextDisabled: next.disabled };
    restoreConfirmed();
  } catch (error) {
    if (current !== requestId) return;
    const reason = error.message === 'revision_mismatch' ? 'Данные изменились во время загрузки.' : errorText(error);
    if (confirmed) {
      restoreConfirmed();
      status.textContent = `Не удалось обновить. Показан последний согласованный результат с датой расчёта в сводке. Сортировка и фильтры сохранены для показанных строк. ${reason} Повторите загрузку.`;
    } else {
      summary.replaceChildren(el('p', 'Сводка недоступна.'));
      status.textContent = `Не удалось обновить. ${reason}`;
    }
    retry.hidden = false;
  } finally { if (current === requestId) { region.setAttribute('aria-busy','false'); summary.setAttribute('aria-busy','false'); } }
}
function changeSort() { offset=0; loadCustomers(); }
form.addEventListener('submit', event => { event.preventDefault(); query=form.elements.q.value.trim(); offset=0; loadCustomers(); });
document.querySelector('#reset').addEventListener('click', () => { form.elements.q.value=''; form.elements.risk_level.value=''; query=''; offset=0; loadCustomers(); });
sortBy.addEventListener('change', changeSort);
direction.addEventListener('click', () => { order=order==='asc'?'desc':'asc'; changeSort(); });
for (const th of document.querySelectorAll('[data-sort]')) th.querySelector('button').addEventListener('click', () => { order=sortBy.value===th.dataset.sort && order==='asc'?'desc':'asc'; sortBy.value=th.dataset.sort; changeSort(); });
previous.addEventListener('click',()=>{offset=Math.max(0,offset-50);loadCustomers();});
next.addEventListener('click',()=>{offset+=50;loadCustomers();});
retry.addEventListener('click',loadCustomers);
sortState(); urlState();
try { await startShell('customers'); await loadCustomers(); } catch (error) { status.textContent=errorText(error); }
