import { el, date } from './shell.js';
const root = document.querySelector('#churn-chart');
let shown = false;
let shownRevision;
const labels = { low: 'Низкий', medium: 'Средний', high: 'Высокий', unknown: 'Без оценки' };
function bar(value, max, scenario=false) {
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 100 20'); svg.setAttribute('preserveAspectRatio','none');
  svg.setAttribute('aria-hidden','true'); svg.classList.add('churn-bar');
  const rect=document.createElementNS(svg.namespaceURI,'rect');
  rect.setAttribute('width',String(value / max * 100)); rect.setAttribute('height','16'); rect.setAttribute('y','2');
  rect.setAttribute('class',scenario?'churn-bar__scenario':'churn-bar__fact'); svg.append(rect); return svg;
}
export function chartUnavailable(message='График временно недоступен.') {
  const status=root.querySelector('[data-chart-status]');
  const text=shown ? `График не обновлён. Показан прежний расчёт с указанной ниже датой; он может отличаться от обновлённого рейтинга. ${message}` : message;
  if(status)status.textContent=text;
  else {const notice=el('p',text);notice.dataset.chartStatus='';notice.setAttribute('role','status');root.append(notice);}
  root.setAttribute('aria-busy','false');
}
export function showChart(data) {
  if(shownRevision===data.analysis_revision){root.querySelector('[data-chart-status]').textContent='График согласован со сводкой и рейтингом.';return;}
  // Keep the native disclosure's keyboard focus and open state during polling.
  const details=root.querySelector('details'), open=details?.open, focused=details?.contains(document.activeElement);
  const content=document.createDocumentFragment();
  content.append(el('h2','Услуги под риском'));
  const status=el('p','График согласован со сводкой и рейтингом.');status.dataset.chartStatus='';status.setAttribute('role','status');content.append(status);
  const figure=el('figure',undefined,'churn-figure');
  figure.append(el('figcaption','Текущий расчёт услуг и условный сценарий непродлений'));
  figure.append(el('h3','Уровни риска сейчас'));
  const maximum=Math.max(1,...Object.values(data.risk_counts));
  for(const [level,count] of Object.entries(data.risk_counts)) {
    const row=el('div',undefined,'churn-row');row.append(el('span',labels[level]),bar(count,maximum),el('span',`${count} услуг`));figure.append(row);
  }
  figure.append(el('h3',`Ближайшее продление в горизонте ${data.horizon_days} дней`));
  figure.append(el('p','Сплошная полоса — текущие услуги высокого риска. Контур — условные непродления среди них при нехватке средств. Шкала: количество услуг; каждая учитывается один раз.'));
  const dueMaximum=Math.max(1,...data.buckets.map(bucket=>bucket.high_risk_services));
  for(const bucket of data.buckets) {
    const group=el('div',undefined,'churn-period');group.append(el('p',bucket.label));
    for(const [key,title,scenario] of [['high_risk_services','Высокий риск',false],['scenario_nonrenewals','Сценарий',true]]) {
      const row=el('div',undefined,'churn-row');row.append(el('span',title),bar(bucket[key],dueMaximum,scenario),el('span',`${bucket[key]} услуг`));group.append(row);
    }
    figure.append(group);
  }
  content.append(figure,el('p',data.scenario_assumption),el('p',`Источник: активный JSON-снимок и оценки услуг ${data.model_version}. Дата расчёта: ${date(data.as_of)}. Данные: ${data.data_revision}; настройки: ${data.settings_revision}; расчёт: ${data.analysis_revision}. Граница горизонта: ${date(data.horizon_end)}. Интервалы считаются от точного времени расчёта в UTC, а не от полуночи.`,'metadata'));
  content.append(el('p',`За горизонтом: ${data.eligible_outside_horizon} возобновляемых услуг. Исключены текущие статусы: deleted — ${data.excluded_current_status_counts.deleted}, blocked — ${data.excluded_current_status_counts.blocked}.`));
  content.append(el('p',`История удалений недоступна. ${data.history.reason}`));
  if(!Object.values(data.risk_counts).some(Boolean))content.append(el('p','Нет возобновляемых услуг для графика. Нулевые значения не означают низкий риск клиентов.'));
  const disclosure=el('details');disclosure.open=Boolean(open);disclosure.append(el('summary','Таблица значений графика'));
  const table=el('table');table.append(el('caption','Те же значения без графического представления'));
  const head=el('thead'), header=el('tr');
  for(const title of ['Интервал / уровень','Текущие услуги','Условные непродления']){const th=el('th',title);th.scope='col';header.append(th);}head.append(header);table.append(head);
  const tbody=el('tbody');
  for(const [level,count] of Object.entries(data.risk_counts)){const tr=el('tr');tr.append(el('td',labels[level]),el('td',count),el('td','Не применяется'));tbody.append(tr);}
  for(const bucket of data.buckets){const tr=el('tr');tr.append(el('td',`${bucket.label} — высокий риск`),el('td',bucket.high_risk_services),el('td',bucket.scenario_nonrenewals));tbody.append(tr);}
  table.append(tbody);const region=el('div',undefined,'table-region');region.tabIndex=0;region.setAttribute('role','region');region.setAttribute('aria-label','Значения графика, таблица с горизонтальной прокруткой');region.append(table);disclosure.append(region);content.append(disclosure);
  root.replaceChildren(content);root.setAttribute('aria-busy','false');shown=true;shownRevision=data.analysis_revision;
  if(focused)disclosure.querySelector('summary').focus({preventScroll:true});
}
