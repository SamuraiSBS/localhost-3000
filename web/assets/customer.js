import { startShell, api, el, eur, score, date, metadata, pairs, list, errorText } from './shell.js';
const detail=document.querySelector('#customer-detail');
function announce(text){if(status.textContent!==text)status.textContent=text;}
const status=document.querySelector('#detail-status');
const retry=document.querySelector('#retry');
const states={active:'Активна',suspended:'Приостановлена',prolong_wait:'Ожидает продления',blocked:'Заблокирована',deleted:'Удалена',performed:'Выполнена',not_performed:'Не выполнена'};
const reasons={no_renewable_services:'Нет возобновляемых услуг',no_eligible_services:'Нет возобновляемых услуг',status_blocked:'Заблокирована: исключена из прогноза',status_deleted:'Удалена: исключена из прогноза',blocked:'Заблокирована: исключена из прогноза',deleted:'Удалена: исключена из прогноза'};
const warningText=(warning,service) => warning === 'prolong_wait_older_than_five_days' ? `Статус источника не согласован со сроком ожидания: окончание ${date(service?.expiresAt)}; ожидание превышает пять дней.` : warning === 'active_expired_over_five_days' ? `Активная услуга просрочена более пяти дней: окончание ${date(service?.expiresAt)}.` : `Предупреждение источника: ${warning}`;
const terms={hour:'час',week:'неделя',month:'месяц',quarter_year:'квартал',half_year:'полгода',year:'год',day:'день'};
function section(title) { const node=el('section',undefined,'section');node.append(el('h2',title));return node; }
let shownRevision=null;
function reconcile(parent,next){
  const old=[...parent.childNodes],fresh=[...next.childNodes];
  fresh.forEach((node,index)=>{
    const current=old[index];if(!current){parent.append(node);return;}
    if(current.nodeType!==node.nodeType||current.nodeName!==node.nodeName){current.replaceWith(node);return;}
    if(node.nodeType===Node.TEXT_NODE){if(current.textContent!==node.textContent)current.textContent=node.textContent;return;}
    for(const attr of [...current.attributes])if(attr.name!=='open'&&!node.hasAttribute(attr.name))current.removeAttribute(attr.name);
    for(const attr of node.attributes)if(attr.name!=='open')current.setAttribute(attr.name,attr.value);
    reconcile(current,node);
  });old.slice(fresh.length).forEach(node=>node.remove());
}
async function readCustomer(background=false,signal) {
  if(!background){detail.setAttribute('aria-busy','true');announce('Загружаем клиента…');}retry.hidden=true;
  try {
    const id=location.pathname.split('/').pop();
    if (!/^[1-9][0-9]*$/.test(id)) { const error=new Error();error.status=404;throw error; }
    const {data}=await api('/customers/'+encodeURIComponent(id),{signal});
    if(stopped)return;
    if(shownRevision===data.analysis_revision){announce('Данные клиента актуальны.');return;}
    const scrollY=window.scrollY, fresh=el('div'); document.querySelector('h1').textContent=data.email;
    const overview=el('section',undefined,'customer-overview'); overview.append(el('h2',`Клиент ${data.id}`),el('p',score(data)));
    if (data.risk_score !== null) { const meter=el('meter',`${data.risk_score} из 100`,'risk-meter');meter.min=0;meter.max=100;meter.value=data.risk_score;meter.setAttribute('aria-label','Индекс риска, баллов из 100');overview.append(meter); }
    if (data.risk_score===null) overview.append(el('p',reasons[data.unscored_reason] || data.unscored_reason || 'Нет возобновляемых услуг'));
    overview.append(list(data.top_reasons.map(item=>item.explanation)),metadata(data));
    if (data.data_quality_warnings.length) overview.append(el('p',`Предупреждений о качестве данных: ${data.data_quality_warnings.length}. Подробности приведены у услуг.`));
    const funds=section('Деньги и ближайшее продление');
    funds.classList.add('customer-funds');
    funds.append(pairs([['Основной баланс',eur(data.balance_cents)],['Бонусы',`${eur(data.bonus_balance_cents)} · ${data.bonusState==='unlocked'?'доступны':'заблокированы'}`],['Реферальные средства',eur(data.withdraw_balance_cents)],['Пополнено за всё время',eur(data.total_replenished_cents)],['Можно потратить',eur(data.spendable_cents)],['Цена ближайших продлений',eur(data.due_services_price_cents)],['Нехватка средств',eur(data.funding_gap_cents)],['Стоимость продлений под риском',eur(data.monetary_risk_cents)]]),el('p',`Горизонт ${data.horizon_days} дней. Одно ближайшее продление каждой услуги, взвешенное индексом риска. Это сценарная оценка в EUR, не подтверждённая вероятность потерь.`),el('p','Реферальные средства не расходуются автоматически. Заблокированные бонусы не покрывают платёж. Исторические пополнения не прибавляются к стоимости под риском.'));
    const services=section('Услуги и расчёт');
    if (!data.services.length) services.append(el('p','Услуг нет.'));
    for (const service of data.services) {
      const node=el('article',undefined,'service'); node.append(el('h3',`${service.name} · ID ${service.id}`),pairs([['Цена периода',eur(service.price_cents)],['Период оплаты',terms[service.paymentTerm]||service.paymentTerm],['Создана',date(service.createdAt)],['Окончание',date(service.expiresAt)],['Статус',states[service.status]||service.status],['Автопродление',service.autoProlong?'Включено':'Отключено'],['Участие в прогнозе',service.eligible?(service.in_horizon?'В горизонте':'За горизонтом'):(reasons[service.excluded_reason]||service.excluded_reason||'Исключена')],['Оценка риска',service.eligible?score(service):'Не оценивается: исключена из прогноза'],['Покрыто средствами',eur(service.covered_cents)],['Не хватает',eur(service.funding_gap_cents)],['Под риском',eur(service.monetary_risk_cents)]]));
      if (service.top_reasons.length) node.append(list(service.top_reasons.slice(0,3).map(item=>item.explanation)));
      for (const warning of service.data_quality_warnings) node.append(el('p',warningText(warning,service)));
      const calculation=el('details');calculation.append(el('summary','Как рассчитано'));
      calculation.append(el('p',`Сумма вкладов: ${service.raw_score ?? 'Нет оценки'}. Ограничение до 100: ${service.score_clamped?'применено':'не применено'}.`));
      for (const feature of service.features) calculation.append(el('h3',feature.title),pairs([['Значение',feature.available?feature.value:'Недоступно'],['Вес',feature.weight],['Вклад, баллов',feature.contribution ?? 'Недоступно']]),el('p',feature.explanation),el('p','Факты: '+Object.entries(feature.evidence).map(([key,value])=>`${key}: ${value}`).join('; ')));
      node.append(calculation);services.append(node);
    }
    const transactions=section('История транзакций');
    transactions.classList.add('customer-transactions');
    const fold=el('details'); fold.append(el('summary',`Транзакции: ${data.transactions.length}`));
    if (!data.transactions.length) fold.append(el('p','Истории транзакций нет. Это не означает высокий риск.'));
    else { const region=el('div',undefined,'table-region');region.setAttribute('role','region');region.setAttribute('aria-label','История транзакций с горизонтальной прокруткой');region.tabIndex=0;const table=el('table');const caption=el('caption','Транзакции клиента');const thead=el('thead');const head=el('tr');for (const text of ['ID','Услуга','Сумма, €','Статус','Тип','Создана','Выполнена']) {const th=el('th',text);th.scope='col';head.append(th);}thead.append(head);const body=el('tbody');for(const transaction of data.transactions){const row=el('tr');for(const text of [transaction.id,transaction.serviceId,eur(transaction.amount_cents),states[transaction.status]||transaction.status,transaction.type,date(transaction.createdAt),date(transaction.performedAt)])row.append(el('td',text));body.append(row);}table.append(caption,thead,body);region.append(table);fold.append(region); }
    transactions.append(fold);fresh.append(overview,funds,services,transactions);reconcile(detail,fresh);window.scrollTo(window.scrollX,scrollY);shownRevision=data.analysis_revision;announce('Данные клиента обновлены.');
  } catch(error) {
    if(error.status===401){stopped=true;document.querySelector('h1').textContent='Клиент';return;}
    if(error.status===404){detail.replaceChildren();shownRevision=null;document.querySelector('h1').textContent='Клиент';announce('404 — Клиент отсутствует в текущем снимке.');retry.hidden=true;return;}
    announce((shownRevision?'Показан последний согласованный результат. ':'')+errorText(error));retry.hidden=false;throw error;
  }
  finally {detail.setAttribute('aria-busy','false');}
}
let timer, delay=2000, running=false, pending=false, stopped=false, ready=false, controller;
function schedule(){clearTimeout(timer);if(!stopped&&!document.hidden)timer=setTimeout(()=>refresh(true),delay);}
async function refresh(background=false){
  if(!ready||stopped||document.hidden&&background)return;
  if(running){if(!background)pending=true;return;}
  running=true;controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),10000);
  try{await readCustomer(background,controller.signal);delay=2000;}catch{delay=Math.min(30000,delay*2);}
  finally{clearTimeout(timeout);running=false;if(pending){pending=false;refresh();}else schedule();}
}
export function loadCustomer(){return refresh();}
addEventListener('focus',()=>refresh(true));addEventListener('online',()=>refresh(true));
document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(timer);else refresh(true);});
addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);controller?.abort();document.querySelector('h1').textContent='Клиент';});
retry.addEventListener('click',loadCustomer);
try{await startShell('customers');ready=true;await loadCustomer();}catch(error){announce(errorText(error));}
