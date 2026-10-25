import { startShell, api, el, eur, score, date, pairs, list, errorText } from './shell.js';

const $ = id => document.getElementById(id);
const titles = {renewal_proximity:'Близость продления',manual_renewal:'Ручное продление',funding_gap:'Нехватка средств',payment_wait:'Ожидание платежа'};
const states = {active:'Активна',suspended:'Приостановлена',prolong_wait:'Ожидает продления',blocked:'Заблокирована',deleted:'Удалена'};
const revision = data => ({analysis_revision:data.analysis_revision,data_revision:data.data_revision,settings_revision:data.settings_revision});
let current = null, customerId = null, generation = 0, controller = null, stale = false;
let sensitivityBusy = false;
let queue = null, queueStale = false, selectedCustomer = null, queueGeneration = 0, queueController = null;
const sameRevision = (left,right) => ['analysis_revision','data_revision','settings_revision'].every(key => left[key] === right[key]);
const reasonTitles = {funding_gap:'Нехватка средств',manual_renewal:'Ручное продление'};
const exclusionTitles = {no_eligible_service:'Нет учитываемых услуг',no_in_horizon_service:'Нет услуг в горизонте',unknown_score:'Нет оценки',low_risk:'Низкий индекс',no_actionable_reason:'Нет причины для действия'};
function clearQueueSelection() {
  selectedCustomer = null; $('worklist-open').disabled = true;
  $('worklist-csv').disabled = true; $('worklist-csv-count').textContent = 'Для CSV отмечено: 0 строк.';
  $('worklist-rows').querySelectorAll('input').forEach(input => { input.checked = false; });
}
function invalidateQueue() {
  if (!queue) return;
  queueStale = true; clearQueueSelection();
  $('worklist-rows').querySelectorAll('input,select,textarea,.workflow-save').forEach(input => { input.disabled = true; });
  $('worklist-selection').setAttribute('aria-disabled','true');
  $('worklist-status').textContent = 'Рабочая версия изменилась. Очередь устарела, выбор сброшен. Обновите очередь.';
}
function observeRevision(data) {
  if (sourcePreview && !sameRevision(sourcePreview,data)) invalidateSourcePreview('Рабочая версия изменилась. Этот предпросмотр устарел; создайте новый по выбранному файлу.');
  if (queue && !sameRevision(queue,data)) invalidateQueue();
  if (current && !sameRevision(current,data)) {
    markObservationsStale();
    stopCalendar();
    if ($('calendar-result').childElementCount) $('calendar-result').dataset.state = 'stale';
    $('calendar-status').textContent = 'Календарь устарел. Черновик сохранён. Обновите рабочую базу и повторите расчёт.';
    stale = true; $('refresh-baseline').hidden = false;
    markSensitivityStale();
    message('Рабочий расчёт и результаты сравнения устарели. Обновите базу; черновик сохранён.');
  }
}
function message(text) { $('lab-status').textContent = text; }
function clearError() { $('lab-error').hidden = true; }
function fail(error) {
  $('lab-error').hidden = false;
  stale = error.status === 409 || stale;
  if (error.status === 409) invalidateQueue();
  $('lab-error').textContent = error.status === 409
    ? 'Рабочая версия изменилась. Результат устарел; черновик сохранён. Обновите базу и повторите сравнение.'
    : error.status === 404 ? 'Клиент отсутствует в текущем снимке. Проверьте ID в рейтинге клиентов.'
    : error.status === 422 ? 'Сценарий не принят. Проверьте веса, порядок порогов, сумму и доступность услуг. Черновик сохранён.'
    : errorText(error);
  $('refresh-baseline').hidden = !current;
}
function cancel() {
  generation++; controller?.abort(); controller = null;
  if (sensitivityBusy) {
    sensitivityBusy = false; $('sensitivity-section').setAttribute('aria-busy','false');
    $('sensitivity-result').dataset.state = 'cancelled';
    $('sensitivity-status').textContent = 'Проверка прервана новым действием. Запустите её снова.';
  }
}
async function operation(fn, timeoutMs = 15000) {
  cancel(); const token = generation; controller = new AbortController();
  const activeController = controller;
  const signal = activeController.signal, timeout = setTimeout(() => activeController.abort(), timeoutMs);
  clearError(); $('lab-content').setAttribute('aria-busy','true');
  try { await fn(signal, () => token === generation); }
  catch (error) { if (token === generation) { fail(error); message('Расчёт не завершён. Введённые значения сохранены.'); } }
  finally { clearTimeout(timeout); if (token === generation) { $('lab-content').setAttribute('aria-busy','false'); controller = null; } }
}
function describeCustomer(data, detailed = false) {
  const fragment = document.createDocumentFragment();
  fragment.append(pairs([['Индекс риска',score(data)],['Основной баланс',eur(data.input.balance)],['Бонусы',`${eur(data.input.bonusBalance)} · ${data.input.bonusState === 'unlocked' ? 'доступны' : 'заблокированы'}`],['Реферальные средства',eur(data.input.withdrawBalance)],['Доступно для продления',eur(data.spendable_cents)],['Ближайшие продления',eur(data.due_services_price_cents)],['Покрыто средствами',eur(data.covered_cents)],['Нехватка средств',eur(data.funding_gap_cents)],['Стоимость под риском',eur(data.monetary_risk_cents)]]));
  if (data.unscored_reason) fragment.append(el('p',`Нет оценки: ${data.unscored_reason}.`));
  fragment.append(el('p','Стоимость под риском — сценарная оценка в EUR, не подтверждённые потери.'));
  if (data.top_reasons.length) fragment.append(list(data.top_reasons.map(reason => reason.explanation)));
  if (!data.services.length) fragment.append(el('p','Услуг нет.'));
  for (const service of data.services) {
    const item = el('article',undefined,'service'), input = service.input;
    item.append(el('h3',`${input.name} · ID ${service.service_id}`),pairs([
      ['Участие в расчёте',service.eligible ? service.in_horizon ? 'В горизонте' : 'За горизонтом' : `Исключена: ${service.excluded_reason}`],
      ['Цена периода',eur(input.price)],['Окончание',date(input.expiresAt)],['Статус',states[input.status] || input.status],['Автопродление',input.autoProlong ? 'Включено' : 'Отключено'],
      ['Индекс риска',score(service)],['Покрыто',eur(service.covered_cents)],['Нехватка',eur(service.funding_gap_cents)],['Стоимость под риском',eur(service.monetary_risk_cents)]
    ]));
    if (service.top_reasons.length) item.append(list(service.top_reasons.map(reason => reason.explanation)));
    if (service.data_quality_warnings.length) item.append(el('p',`Предупреждения источника: ${service.data_quality_warnings.join('; ')}.`));
    if (detailed) {
      const fold = el('details'); fold.append(el('summary','Исходные поля и расчёт'),pairs([
        ['Создана',date(input.createdAt)],['Период оплаты',input.paymentTerm],['Сумма вкладов',service.raw_score ?? 'Нет оценки'],['Ограничение до 100',service.score_clamped ? 'Применено' : 'Не применено']
      ]));
      for (const feature of service.features) fold.append(el('h3',feature.title),pairs([
        ['Значение',feature.available ? feature.value : 'Недоступно'],['Вес',feature.weight],['Вклад, баллов',feature.contribution ?? 'Недоступно']
      ]),el('p',feature.explanation),el('p',`Факты: ${JSON.stringify(feature.evidence)}`));
      item.append(fold);
    }
    fragment.append(item);
  }
  return fragment;
}
function populateModel() {
  const settings = current.effective_settings;
  $('lab-weights').replaceChildren();
  for (const [id,value] of Object.entries(settings.weights)) {
    const wrapper = el('div',undefined,'ds-field'), label = el('label',`${titles[id] || id} — вес`), input = el('input',undefined,'ds-input');
    label.htmlFor = 'lab-weight-'+id; input.id = label.htmlFor; input.dataset.feature = id;
    input.type = 'number'; input.min = '0'; input.max = '100'; input.step = 'any'; input.required = true; input.value = value;
    wrapper.append(label,input); $('lab-weights').append(wrapper);
  }
  $('lab-medium').value = settings.thresholds.medium; $('lab-high').value = settings.thresholds.high;
}
function populateActions(selected = []) {
  $('action-services').replaceChildren();
  const services = current.baseline.services.filter(service => service.input.status === 'active' && service.in_horizon && !service.input.autoProlong);
  for (const service of services) {
    const label = el('label',undefined,'lab-check'), checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.value = service.service_id;
    checkbox.checked = selected.includes(String(service.service_id));
    label.append(checkbox,el('span',`${service.input.name} · ID ${service.service_id}`)); $('action-services').append(label);
  }
  if (!services.length) $('action-services').append(el('p','Нет услуг, для которых можно включить автопродление.'));
  if (selected.some(id => !services.some(service => String(service.service_id) === id))) {
    $('action-services').append(el('p','Некоторые выбранные услуги больше недоступны. Остальные значения черновика сохранены; проверьте выбор перед сравнением.'));
  }
}
async function loadBaseline(preserve = false, expectedQueue = null) {
  const value = $('customer-id').value;
  if (!/^[1-9][0-9]*$/.test(value) || !Number.isSafeInteger(Number(value))) return;
  stopCalendar();
  clearObservations();
  const selected = [...$('action-services').querySelectorAll('input:checked')].map(input => input.value);
  const solverSelection = solverChoices();
  const same = preserve && customerId === Number(value);
  // Hide the old customer's data while fetching a different customer.
  if (!same) { current = null; $('lab-content').hidden = true; $('solver-section').hidden = true; }
  await operation(async (signal, valid) => {
    message('Загружаем согласованный рабочий расчёт…');
    const customer = (await api('/customers/'+value,{signal})).data;
    if (!valid()) return;
    observeRevision(customer);
    if (expectedQueue && !sameRevision(expectedQueue,customer)) {
      invalidateQueue(); message('Версия очереди изменилась. Обновите очередь и выберите клиента снова.'); return;
    }
    const result = (await api('/lab/preview',{method:'POST',signal,body:JSON.stringify({customer_id:Number(value),expected_revision:revision(customer),mode:'action',action:{top_up_cents:0,enable_auto_prolong_ids:[]}})})).data;
    if (!valid()) return;
    observeRevision(result);
    current = result; customerId = Number(value); stale = false;
    $('baseline').replaceChildren(describeCustomer(result.baseline,true));
    $('baseline-title').textContent = `Рабочий расчёт · ${result.baseline.input.email}`;
    $('lab-revision').replaceChildren(el('p',`Расчёт: ${result.analysis_revision} · данные: ${result.data_revision} · настройки: ${result.settings_revision}.`),el('p',`Дата: ${date(result.as_of)} · горизонт: ${result.horizon_days} дней · модель: ${result.model_version}.`));
    if (!same) { populateModel(); $('top-up').value = '0'; }
    populateActions(same ? selected : []);
    populateCalendar(same);
    populateSolver(same ? solverSelection : null);
    $('sensitivity-result').replaceChildren(); delete $('sensitivity-result').dataset.state; $('sensitivity-error').hidden = true;
    $('sensitivity-status').textContent = 'Рабочая база загружена. Запустите новую проверку чувствительности.';
    $('model-result').replaceChildren(); $('action-result').replaceChildren();
    $('lab-content').hidden = false; $('refresh-baseline').hidden = true;
    history.replaceState(null,'','/lab?customer_id='+value);
    message(same ? 'Рабочая база обновлена. Черновик сохранён; повторите сравнение.' : 'Рабочий расчёт загружен. Выберите сценарий.');
  });
}
let observationGeneration = 0, observationController = null, observationOffset = null, observationBaseline = null;
const observationIds = new Set();
function stopObservations() {
  observationGeneration++; observationController?.abort(); observationController = null;
  $('observations-section').setAttribute('aria-busy','false'); $('observations-refresh').disabled = false; $('observations-more').disabled = false;
}
function clearObservations() {
  stopObservations(); observationOffset = null; observationBaseline = null; observationIds.clear();
  $('observations-result').replaceChildren(); $('observations-metadata').replaceChildren(); $('observations-more').hidden = true;
  $('observations-error').hidden = true; $('observations-status').textContent = 'История ещё не загружена.';
}
function markObservationsStale() {
  stopObservations(); observationOffset = null; $('observations-more').hidden = true;
  if (observationIds.size) $('observations-status').textContent = 'Рабочая версия изменилась. Обновите историю. Черновики сценариев сохранены.';
}
function observationValue(value) {
  if (value === null || value === undefined) return 'Нет значения';
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет';
  return typeof value === 'object' ? JSON.stringify(value,null,2) : String(value);
}
function observationItem(event) {
  const item = el('article',undefined,'observation-event'); item.dataset.eventId = event.event_id; item.dataset.kind = event.kind;
  if (event.customer_id) item.dataset.customerId = event.customer_id;
  const kinds = {source:'Изменение источника',workflow:'Ручная работа',model:'Глобальное изменение модели'};
  item.append(el('h3',kinds[event.kind] || event.kind),pairs([['Получено сервером',date(event.received_at)],['Происхождение',event.kind === 'source' ? 'Публикация полного снимка источника' : event.kind === 'workflow' ? 'Сохранённая запись оператора' : 'Публикация настроек для всех клиентов']]));
  if (event.kind === 'source') {
    const fields = {balance:'Баланс',autoProlong:'Автопродление',status:'Статус',disappeared:'Запись исчезла из полного снимка'};
    item.append(pairs([
      ['Клиент',event.customer_id],['Объект',`${event.entity === 'account' ? 'Аккаунт' : 'Услуга'} ${event.entity_id}`],['Изменение',fields[event.field] || event.field],
      ['Версии данных',`${event.previous_data_revision} → ${event.data_revision}`],['Настройки сравнения',event.comparison_settings_revision],['Модель сравнения',event.comparison_model_version],['Фиксированная дата сравнения',date(event.comparison_as_of)]
    ]));
    if (event.field === 'disappeared') item.append(el('p','Запись отсутствует в новом полном снимке. Это наблюдение об источнике, а не установленная причина исчезновения.'));
  } else if (event.kind === 'workflow') {
    item.append(pairs([['Клиент',event.customer_id],['Автор',event.author],['Версии записи',`${event.previous_workflow_revision} → ${event.workflow_revision}`]]));
  } else item.append(pairs([['Область','Все клиенты'],['Версии настроек',`${event.previous_settings_revision} → ${event.settings_revision}`],['Дата сравнения',date(event.comparison_as_of)]]));
  const changeLabels = {status:'Статус работы',note:'Заметка',next_contact_at:'Следующий контакт',manual_outcome:'Ручной исход'};
  if (event.kind === 'workflow') {
    for (const [field,change] of Object.entries(event.changes || {})) item.append(el('h4',changeLabels[field] || field),pairs([['До',observationValue(change.before)],['После',observationValue(change.after)]]));
  } else item.append(pairs([['До',observationValue(event.before)],['После',observationValue(event.after)]]));
  return item;
}
async function loadObservations(more = false) {
  if (!current || (more && observationOffset === null)) return;
  const selectedId = customerId, offset = more ? observationOffset : 0;
  stopObservations(); const token = observationGeneration;
  if (!more) { observationIds.clear(); observationBaseline = null; observationOffset = null; $('observations-result').replaceChildren(); $('observations-metadata').replaceChildren(); }
  $('observations-error').hidden = true; $('observations-more').hidden = true;
  $('observations-section').setAttribute('aria-busy','true'); $('observations-refresh').disabled = true;
  $('observations-status').textContent = `Загружаем историю клиента ${selectedId}…`;
  const active = new AbortController(); observationController = active; const timeout = setTimeout(() => active.abort(),15000);
  try {
    const data = (await api(`/observations?customer_id=${encodeURIComponent(selectedId)}&limit=50&offset=${offset}`,{signal:active.signal})).data;
    if (token !== observationGeneration || selectedId !== customerId) return;
    if (more && observationBaseline !== data.source_baseline_revision) {
      observationOffset = null; $('observations-status').textContent = 'Источник изменился между страницами. Обновите историю с начала. Черновики сохранены.'; return;
    }
    observationBaseline = data.source_baseline_revision;
    $('observations-metadata').replaceChildren(pairs([['Клиент',selectedId],['Курсор непрерывности источника',data.source_baseline_revision ?? 'Ещё не установлен']]));
    $('observations-metadata').append(el('p','Курсор непрерывности — служебная граница текущего источника, не историческое событие и не доказательство полноты истории.'));
    for (const event of data.events) { if (!observationIds.has(event.event_id)) { observationIds.add(event.event_id); $('observations-result').append(observationItem(event)); } }
    observationOffset = data.has_more && data.next_offset !== null && data.next_offset < 500 ? data.next_offset : null;
    $('observations-more').hidden = observationOffset === null;
    $('observations-status').textContent = observationIds.size ? `История клиента ${selectedId}: показано ${observationIds.size} событий.${data.has_more && observationOffset === null ? ' Достигнут предел просмотра. Обновите историю для нового просмотра.' : ''}` : 'Наблюдений для клиента и глобальных изменений модели пока нет. Более ранняя история неизвестна.';
  } catch (error) {
    if (token !== observationGeneration) return;
    $('observations-error').hidden = false; $('observations-error').textContent = errorText(error);
    $('observations-status').textContent = 'История не загружена. Повторите запрос; черновики сохранены.';
    $('observations-more').hidden = observationOffset === null;
  } finally { clearTimeout(timeout); if (token === observationGeneration) { observationController = null; $('observations-section').setAttribute('aria-busy','false'); $('observations-refresh').disabled = false; } }
}
$('observations-refresh').addEventListener('click',() => loadObservations());
$('observations-more').addEventListener('click',() => loadObservations(true));
$('customer-id').addEventListener('input',clearObservations);
$('logout').addEventListener('click',clearObservations,{capture:true});
addEventListener('pagehide',clearObservations);
const calendarPeriods = {hour:'Час',week:'Неделя',month:'Месяц',quarter_year:'Квартал',half_year:'Полгода',year:'Год'};
let calendarGeneration = 0, calendarController = null;
function stopCalendar() {
  calendarGeneration++; calendarController?.abort(); calendarController = null;
  $('calendar-section').setAttribute('aria-busy','false'); $('calendar-form').querySelector('button').disabled = false;
}
function populateCalendar(preserve) {
  const previous = preserve ? new Map([...$('calendar-periods').querySelectorAll('select')].map(input => [input.dataset.service,input.value])) : new Map();
  $('calendar-periods').replaceChildren();
  for (const service of current.baseline.services) {
    const wrapper = el('div',undefined,'ds-field'), label = el('label',`Услуга ${service.service_id} · ${service.input.name}`), select = el('select',undefined,'ds-input');
    select.id = 'calendar-period-'+service.service_id; select.dataset.service = String(service.service_id); label.htmlFor = select.id;
    const source = el('option',`Из источника: ${calendarPeriods[service.input.paymentTerm] || service.input.paymentTerm || 'Период неизвестен'}`); source.value = ''; select.append(source);
    for (const [value,title] of Object.entries(calendarPeriods)) { const option = el('option',`Допущение: ${title}`); option.value = value; select.append(option); }
    select.value = previous.get(String(service.service_id)) || ''; wrapper.append(label,select); $('calendar-periods').append(wrapper);
  }
  if (!current.baseline.services.length) $('calendar-periods').append(el('p','Услуг нет. Календарь будет пустым.'));
  $('calendar-result').replaceChildren(); delete $('calendar-result').dataset.state; $('calendar-error').hidden = true;
  $('calendar-status').textContent = preserve ? 'Рабочая база обновлена. Допущения сохранены; повторите расчёт.' : 'Выберите горизонт и допущения.';
}
function renderCalendar(data) {
  const result = $('calendar-result'), scenario = data.scenario;
  result.dataset.state = scenario.status;
  result.replaceChildren(el('h3','Гипотетический календарь'),pairs([
    ['Клиент',customerId],['Дата расчёта',date(scenario.as_of)],['Конец горизонта сценария',date(scenario.horizon_end)],['Горизонт сценария, дней',scenario.horizon_days],
    ['Событий показано',scenario.events.length],['Начальный бюджет',eur(scenario.spendable_cents)],['Стоимость событий',eur(scenario.total_price_cents)],
    ['Покрыто',eur(scenario.total_covered_cents)],['Нехватка',eur(scenario.funding_gap_cents)],['Остаток',eur(scenario.remaining_cents)],
    ['Первая нехватка в сценарии',scenario.first_shortfall_at ? date(scenario.first_shortfall_at) : 'Нет среди показанных событий']
  ]),el('p',`Рабочий горизонт: ${data.horizon_days} дней. Расчёт ${data.analysis_revision} · данные ${data.data_revision} · настройки ${data.settings_revision}.`));
  if (scenario.truncated) result.append(el('p','Достигнут лимит событий. Итоги и первая нехватка относятся только к показанной части календаря.'));
  if (scenario.status === 'insufficient_data') result.append(el('p','Недостаточно данных о периодах. Расчёт частичный: проверьте исключённые услуги и задайте явные допущения.'));
  result.append(el('h3','Происхождение периодов'));
  const overrides = new Map(scenario.period_assumptions.map(item => [item.service_id,item.payment_term]));
  const serviceById = new Map(data.baseline.services.map(service => [service.service_id,service]));
  for (const service of data.baseline.services) {
    const sourceTerm = service.input.paymentTerm, override = overrides.get(service.service_id);
    result.append(el('p',`Услуга ${service.service_id}: ${override ? `допущение пользователя — ${calendarPeriods[override] || override}` : `источник paymentTerm — ${calendarPeriods[sourceTerm] || sourceTerm || 'неизвестен'}`}. Исходный период: ${sourceTerm || 'неизвестен'}. Цена периода: ${eur(service.input.price)}.`));
  }
  const exclusions = {outside_horizon:'За горизонтом сценария',blocked:'Заблокирована',deleted:'Удалена',unsupported_status:'Статус не поддерживается','insufficient_data:unsupported_payment_term':'Недостаточно данных: период отсутствует или не поддерживается'};
  result.append(el('h3','Исключённые услуги'));
  result.append(scenario.exclusions.length ? list(scenario.exclusions.map(item => `Услуга ${item.service_id}: ${exclusions[item.reason] || item.reason}`)) : el('p','Исключений нет.'));
  const events = el('details'), summary = el('summary',`События продлений: ${scenario.events.length}`); events.append(summary);
  if (!scenario.events.length) events.append(el('p','В выбранном горизонте нет событий с достаточными данными.'));
  const eventList = el('ol',undefined,'calendar-events');
  for (const event of scenario.events) {
    const item = el('li'), service = serviceById.get(event.service_id), assumption = overrides.get(event.service_id);
    item.append(el('h3',`Услуга ${event.service_id} · ${event.occurrence_index === 0 ? 'Первое продление' : `Повтор ${event.occurrence_index}`}`),pairs([
      ['Дата события',date(event.due_at)],['Период',calendarPeriods[assumption || service?.input.paymentTerm] || 'Неизвестен'],['Происхождение',assumption ? 'Явное допущение пользователя' : 'Источник paymentTerm'],
      ['Стоимость',eur(event.price_cents)],['Покрыто',eur(event.covered_cents)],['Нехватка',eur(event.funding_gap_cents)],['Остаток',eur(event.remaining_cents)]
    ])); eventList.append(item);
  }
  events.append(eventList); result.append(events);
}
async function calculateCalendar() {
  if (!current || !$('calendar-form').reportValidity()) return;
  if (stale) { $('calendar-status').textContent = 'Рабочая база устарела. Черновик сохранён. Обновите базу перед расчётом календаря.'; return; }
  stopCalendar(); const token = calendarGeneration, selectedId = customerId;
  const active = new AbortController(); calendarController = active; const timeout = setTimeout(() => active.abort(),15000);
  const body = {customer_id:selectedId,expected_revision:revision(current),horizon_days:Number($('calendar-horizon').value),max_events:Number($('calendar-max-events').value),period_assumptions:[...$('calendar-periods').querySelectorAll('select')].filter(input => input.value).map(input => ({service_id:Number(input.dataset.service),payment_term:input.value}))};
  $('calendar-section').setAttribute('aria-busy','true'); $('calendar-form').querySelector('button').disabled = true; $('calendar-error').hidden = true;
  $('calendar-status').textContent = 'Рассчитываем гипотетический календарь…'; $('calendar-result').replaceChildren();
  try {
    const data = (await api('/lab/renewal-calendar',{method:'POST',signal:active.signal,body:JSON.stringify(body)})).data;
    if (token !== calendarGeneration || customerId !== selectedId) return;
    observeRevision(data);
    if (token !== calendarGeneration) return;
    renderCalendar(data); $('calendar-status').textContent = 'Сценарий рассчитан. Рабочий расчёт и черновики других сценариев сохранены.';
  } catch (error) {
    if (token !== calendarGeneration) return;
    if (error.status === 409) { stale = true; invalidateQueue(); $('refresh-baseline').hidden = false; }
    $('calendar-error').hidden = false;
    $('calendar-error').textContent = error.status === 409 ? 'Рабочая версия изменилась. Черновик календаря сохранён. Обновите базу и повторите расчёт.' : error.status === 422 ? 'Проверьте горизонт, лимит событий и выбранные периоды. Черновик сохранён.' : errorText(error);
    $('calendar-status').textContent = 'Календарь не рассчитан. Введённые значения сохранены.';
  } finally { clearTimeout(timeout); if (token === calendarGeneration) { calendarController = null; $('calendar-section').setAttribute('aria-busy','false'); $('calendar-form').querySelector('button').disabled = false; } }
}
$('calendar-form').addEventListener('submit',event => {event.preventDefault();calculateCalendar();});
$('calendar-form').addEventListener('input',() => { stopCalendar(); $('calendar-result').replaceChildren(); $('calendar-error').hidden = true; $('calendar-status').textContent = 'Черновик календаря изменён. Запустите новый расчёт.'; });
$('customer-id').addEventListener('input',stopCalendar);
$('logout').addEventListener('click',() => {stopCalendar();$('calendar-result').replaceChildren();$('calendar-periods').replaceChildren();},{capture:true});
addEventListener('pagehide',stopCalendar);
const workflowStatuses = {new:'Новый',in_progress:'В работе',deferred:'Отложен',processed:'Обработан'};
const workflowOutcomes = {none:'Не указан',contacted:'Контакт состоялся',topup_reported:'Сообщено о пополнении',renewal_reported:'Сообщено о продлении',no_response:'Нет ответа',declined:'Отказ'};
function workflowEditor(id) {
  const fold = el('details',undefined,'workflow-editor'), summary = el('summary',`Работа с клиентом ${id}`);
  const form = el('form'), fields = el('fieldset'), feedback = el('p'), metadata = el('div');
  feedback.setAttribute('role','status'); feedback.setAttribute('aria-live','polite');
  const controls = {};
  for (const [key,title,options] of [
    ['status','Статус',workflowStatuses],['note','Заметка',null],
    ['next_contact_at','Следующий контакт — местное время устройства',null],['manual_outcome','Ручной исход',workflowOutcomes]
  ]) {
    const wrapper = el('div',undefined,'ds-field'), label = el('label',title);
    const input = el(options ? 'select' : key === 'note' ? 'textarea' : 'input',undefined,'ds-input');
    input.id = `workflow-${id}-${key}`; label.htmlFor = input.id; input.name = key;
    if (options) for (const [value,text] of Object.entries(options)) { const option = el('option',text); option.value = value; input.append(option); }
    else if (key === 'note') { input.maxLength = 2000; input.rows = 4; }
    else input.type = 'datetime-local';
    controls[key] = input; wrapper.append(label,input); fields.append(wrapper);
  }
  const save = el('button','Сохранить работу с клиентом','ds-button workflow-save'); save.type = 'submit';
  const reload = el('button','Загрузить сохранённую запись','ds-button ds-button--secondary'); reload.type = 'button';
  let record = null, initialContact = '', busy = false, conflicted = false;
  fields.disabled = true; save.disabled = true;
  function showRecord(data) {
    record = data; conflicted = false;
    controls.status.value = data.status; controls.note.value = data.note; controls.manual_outcome.value = data.manual_outcome;
    const contact = data.next_contact_at ? new Date(data.next_contact_at) : null;
    initialContact = contact ? new Date(contact.getTime()-contact.getTimezoneOffset()*60000).toISOString().slice(0,16) : '';
    controls.next_contact_at.value = initialContact;
    summary.textContent = `Работа с клиентом ${id} · ${workflowStatuses[data.status]}`;
    metadata.replaceChildren(pairs([['Автор (сервер)',data.author ?? 'Ещё нет записи'],['Изменено (сервер)',date(data.updated_at)],['Версия записи',data.revision]]));
    fields.disabled = queueStale; save.disabled = queueStale;
  }
  async function load() {
    if (busy) return;
    busy = true; form.setAttribute('aria-busy','true'); save.disabled = true; reload.disabled = true;
    feedback.textContent = 'Загружаем сохранённую запись…';
    try { showRecord((await api(`/customers/${id}/workflow`)).data); feedback.textContent = 'Запись загружена. Сохранение — отдельное действие.'; }
    catch (error) { feedback.textContent = errorText(error); }
    finally { busy = false; form.setAttribute('aria-busy','false'); reload.disabled = false; save.disabled = !record || conflicted || queueStale; }
  }
  form.addEventListener('submit',async event => {
    event.preventDefault();
    if (busy || !record || conflicted || queueStale || !form.reportValidity()) return;
    const contactValue = controls.next_contact_at.value;
    if (controls.status.value === 'deferred' && (record.status !== 'deferred' || contactValue !== initialContact) && (!contactValue || new Date(contactValue) <= new Date())) {
      feedback.textContent = 'Для отсрочки укажите будущую дату следующего контакта.'; controls.next_contact_at.focus(); return;
    }
    const patch = {expected_revision:record.revision,status:controls.status.value,note:controls.note.value,manual_outcome:controls.manual_outcome.value};
    if (contactValue !== initialContact) patch.next_contact_at = contactValue ? new Date(contactValue).toISOString() : null;
    busy = true; save.disabled = true; reload.disabled = true; fields.disabled = true; form.setAttribute('aria-busy','true');
    try {
      const metadata = (await api('/customers?limit=1')).data; observeRevision(metadata);
      if (queueStale || !sameRevision(queue,metadata)) { feedback.textContent = 'Очередь устарела. Обновите очередь перед сохранением.'; return; }
      showRecord((await api(`/customers/${id}/workflow`,{method:'PATCH',body:JSON.stringify(patch)})).data);
      feedback.textContent = 'Запись сохранена. Ручной исход не подтверждает изменение источника.';
    } catch (error) {
      conflicted = error.status === 409;
      feedback.textContent = conflicted ? 'Запись изменена другим оператором. Черновик сохранён. Загрузите сохранённую запись перед новой правкой.' : error.status === 422 ? 'Проверьте заметку и будущую дату отсрочки. Черновик сохранён.' : errorText(error);
    } finally { busy = false; fields.disabled = queueStale; save.disabled = conflicted || queueStale; reload.disabled = false; form.setAttribute('aria-busy','false'); }
  });
  reload.addEventListener('click',load);
  fold.addEventListener('toggle',() => { if (fold.open && !record) load(); });
  fields.append(save); form.append(fields,reload,feedback,metadata);
  fold.append(summary,el('p','Заметка сохраняется как обычный текст. Ручной исход — сообщение оператора, а не подтверждённое событие источника. Отсрочка возобновляется вручную; сообщения клиенту не отправляются.'),form);
  return fold;
}
let qualityGeneration = 0;
async function loadQuality() {
  const token = ++qualityGeneration;
  $('quality-section').setAttribute('aria-busy','true'); $('quality-refresh').disabled = true; $('quality-error').hidden = true;
  $('quality-result').replaceChildren(); $('quality-status').textContent = 'Проверяем опубликованный снимок…';
  try {
    const data = (await api('/data-quality')).data;
    if (token !== qualityGeneration) return;
    observeRevision(data);
    const freshness = {fresh:'Получен не более 24 часов назад',stale:'Получен более 24 часов назад',no_timestamp:'Время получения неизвестно',future_timestamp:'Время получения находится в будущем'};
    const coverage = value => `${value.numerator} из ${value.denominator}; без оценки: ${value.unknown}`;
    $('quality-result').replaceChildren(pairs([
      ['Дата расчёта (as_of)',date(data.as_of)],['Снимок получен (received_at)',date(data.received_at)],['Дата проверки',date(data.checked_at)],
      ['Свежесть · фиксированный порог 24 часа',freshness[data.freshness.state] || data.freshness.state],
      ['Покрытие индексом',coverage(data.score_coverage)],['Покрытие денежной оценкой',coverage(data.monetary_coverage)],['Предупреждений полей',data.warning_count]
    ]),el('p',`Расчёт ${data.analysis_revision} · данные ${data.data_revision} · настройки ${data.settings_revision}.`));
    for (const [title,groups] of [['Причины отсутствия оценки',data.unknown_reasons],['Предупреждения полей',data.field_warnings]]) {
      $('quality-result').append(el('h3',title));
      if (!groups.length) $('quality-result').append(el('p','Не обнаружены в этом снимке.'));
      for (const group of groups) {
        const section = el('div',undefined,'quality-group');
        section.append(el('p',`${group.code}: ${group.count}${group.customer_count === undefined ? ' клиентов' : ` предупреждений у ${group.customer_count} клиентов`}.`));
        const links = el('ul');
        for (const id of group.customer_ids) { const item = el('li'), link = el('a',`Открыть клиента ${id}`); link.href = '/lab?customer_id='+encodeURIComponent(id); item.append(link); links.append(item); }
        section.append(links); $('quality-result').append(section);
      }
    }
    $('quality-status').textContent = 'Отчёт получен. Полнота и достоверность источника не установлены.';
  } catch (error) { if (token === qualityGeneration) { $('quality-error').hidden = false; $('quality-error').textContent = errorText(error); $('quality-status').textContent = 'Отчёт не получен. Повторите проверку.'; } }
  finally { if (token === qualityGeneration) { $('quality-refresh').disabled = false; $('quality-section').setAttribute('aria-busy','false'); } }
}
$('quality-refresh').addEventListener('click',loadQuality);
function clearContactData() {
  qualityGeneration++; $('quality-result').replaceChildren(); $('worklist-rows').replaceChildren();
}
$('logout').addEventListener('click',clearContactData,{capture:true});
addEventListener('pagehide',clearContactData);
function renderWorklist(data) {
  $('worklist-summary').replaceChildren(
    el('p',`Показано ${data.returned_count} из ${data.matched_count} подходящих клиентов. Расчёт ${data.analysis_revision}, данные ${data.data_revision}, настройки ${data.settings_revision}.`),
    pairs([['Всего клиентов',data.coverage.total_customers],['С оценкой',data.coverage.scored_customers],['Без оценки',data.coverage.unknown_customers],['С денежной оценкой',data.coverage.monetary_known_customers],['Без денежной оценки',data.coverage.monetary_unknown_customers]])
  );
  const exclusions = Object.entries(data.excluded_reason_counts);
  $('worklist-summary').append(el('h3','Исключения'),exclusions.length
    ? list(exclusions.map(([reason,count]) => `${exclusionTitles[reason] || reason}: ${count}`)) : el('p','Исключённых клиентов нет.'));
  $('worklist-rows').replaceChildren();
  $('worklist-selection').hidden = !data.rows.length;
  $('worklist-selection').setAttribute('aria-disabled','false');
  if (!data.rows.length) $('worklist-summary').append(el('p','Нет клиентов с подходящими причинами для контакта.'));
  for (const row of data.rows) {
    const item = el('article',undefined,'lab-worklist-row'), label = el('label',undefined,'lab-check'), radio = el('input');
    radio.type = 'radio'; radio.name = 'worklist-customer'; radio.value = row.customer_id;
    label.append(radio,el('span',`Клиент ${row.customer_id}`));
    radio.addEventListener('change',() => {
      if (queueStale) return;
      selectedCustomer = row.customer_id; $('worklist-open').disabled = false;
    });
    const csvLabel = el('label',undefined,'lab-check'), checkbox = el('input');
    checkbox.type = 'checkbox'; checkbox.value = row.customer_id; checkbox.className = 'csv-customer';
    csvLabel.append(checkbox,el('span',`В CSV: клиент ${row.customer_id}`));
    checkbox.addEventListener('change',() => {
      const count = $('worklist-rows').querySelectorAll('.csv-customer:checked').length;
      $('worklist-csv').disabled = queueStale || !count;
      $('worklist-csv-count').textContent = `Для CSV отмечено: ${count} строк.`;
    });
    item.append(csvLabel);
    item.append(label,pairs([['Индекс риска',score(row)],['Стоимость под риском',eur(row.monetary_risk_cents)],['Ближайшие продления',eur(row.due_services_price_cents)],['Нехватка средств',eur(row.funding_gap_cents)]]));
    for (const service of row.affected_services) {
      item.append(el('h3',`Услуга ${service.service_id}`),el('p',`${service.overdue ? 'Просрочена' : 'Окончание'}: ${date(service.expires_at)}. ${states[service.status] || service.status}.`),
        el('p',`Цена: ${eur(service.price_cents)}. Нехватка: ${eur(service.funding_gap_cents)}.`),
        list(service.reason_codes.map(reason => reasonTitles[reason] || reason)),
        list(service.suggested_actions.map(action => action === 'review_funding_gap' ? 'Уточнить нехватку средств с клиентом.' : 'Обсудить ручное продление и возможность автопродления.')));
    }
    if (row.warnings.length) item.append(el('p','Предупреждения: '+row.warnings.map(warning => warning === 'prolong_wait' ? 'Источник сообщает ожидание продления; контакт или пополнение не меняет статус услуги.' : warning).join('; ')));
    item.append(workflowEditor(row.customer_id));
    $('worklist-rows').append(item);
  }
}
async function loadWorklist() {
  if (!$('worklist-form').reportValidity()) return;
  const token = ++queueGeneration; queueController?.abort(); queueController = new AbortController();
  const activeController = queueController, timeout = setTimeout(() => activeController.abort(),15000);
  clearQueueSelection(); queueStale = true;
  $('worklist-rows').querySelectorAll('input,select,textarea,.workflow-save').forEach(input => { input.disabled = true; });
  $('worklist-selection').setAttribute('aria-disabled','true');
  $('worklist-error').hidden = true; $('worklist-form').setAttribute('aria-busy','true');
  $('worklist-status').textContent = 'Рассчитываем очередь по рабочей базе…';
  try {
    const metadata = (await api('/customers?limit=1',{signal:activeController.signal})).data;
    if (token !== queueGeneration) return;
    observeRevision(metadata);
    const data = (await api('/lab/worklist',{method:'POST',signal:activeController.signal,body:JSON.stringify({expected_revision:revision(metadata),limit:Number($('worklist-limit').value)})})).data;
    if (token !== queueGeneration) return;
    observeRevision(data); queue = data; queueStale = false;
    renderWorklist(data); $('worklist-status').textContent = 'Очередь рассчитана по рабочему снимку. Выбор хранится только в этой вкладке.';
  } catch (error) {
    if (token !== queueGeneration) return;
    invalidateQueue(); $('worklist-error').hidden = false;
    $('worklist-error').textContent = error.status === 409 ? 'Рабочая версия изменилась во время расчёта. Обновите очередь.' : errorText(error);
    $('worklist-status').textContent = 'Очередь не обновлена. Повторите запрос.';
  } finally {
    clearTimeout(timeout);
    if (token === queueGeneration) { queueController = null; $('worklist-form').setAttribute('aria-busy','false'); }
  }
}
async function openWorklistCustomer() {
  if (!queue || queueStale || selectedCustomer === null) return;
  $('customer-id').value = String(selectedCustomer);
  await loadBaseline(false,revision(queue));
  if (!queueStale && current && customerId === selectedCustomer) $('baseline-title').scrollIntoView({block:'start',behavior:'instant'});
}
$('worklist-form').addEventListener('submit',event => {event.preventDefault();loadWorklist();});
$('worklist-limit').addEventListener('input',() => {
  queueGeneration++; queueController?.abort(); queueController = null;
  $('worklist-form').setAttribute('aria-busy','false'); invalidateQueue();
});
$('worklist-open').addEventListener('click',openWorklistCustomer);
let csvBusy = false;
async function downloadCsv(scope) {
  if (csvBusy || (scope === 'worklist' && (!queue || queueStale))) return;
  csvBusy = true; $('csv-error').hidden = true;
  const button = scope === 'filtered' ? $('csv-filter').querySelector('button') : $('worklist-csv');
  button.disabled = true; button.setAttribute('aria-busy','true');
  const abort = new AbortController(), timeout = setTimeout(() => abort.abort(),15000);
  try {
    const filters = {q:$('csv-query').value,risk_level:$('csv-risk').value || null,sort_by:'risk_score',sort_order:'desc'};
    const ids = [...$('worklist-rows').querySelectorAll('.csv-customer:checked')].map(input => Number(input.value));
    const metadata = scope === 'worklist' ? queue : (await api('/customers?'+new URLSearchParams({q:filters.q,...(filters.risk_level ? {risk_level:filters.risk_level} : {}),limit:'1'}),{signal:abort.signal})).data;
    const expected = revision(metadata), selectionToken = queueGeneration;
    $('csv-status').textContent = `Область: ${scope === 'filtered' ? 'вся выборка фильтра' : 'отмеченные строки очереди'}. Строк: ${scope === 'filtered' ? metadata.total : ids.length}. Расчёт ${expected.analysis_revision}; данные ${expected.data_revision}; настройки ${expected.settings_revision}. Готовим CSV…`;
    const session = (await api('/auth/me',{signal:abort.signal})).data;
    const response = await fetch('/api/v1/exports/customers.csv',{method:'POST',credentials:'same-origin',cache:'no-store',signal:abort.signal,
      headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},
      body:JSON.stringify({expected_revision:expected,scope,...(scope === 'filtered' ? {filters} : {customer_ids:ids})})});
    if (!response.ok) {
      if (response.status === 401) await api('/auth/me');
      throw {status:response.status};
    }
    const blob = await response.blob();
    if (scope === 'worklist' && (queueStale || queueGeneration !== selectionToken)) return;
    const url = URL.createObjectURL(blob), link = el('a');
    link.href = url; link.download = `customers-${scope}.csv`; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url),1000);
    $('csv-status').textContent = `CSV готов. Область: ${scope === 'filtered' ? 'вся выборка фильтра' : 'отмеченные строки очереди'}. Строк: ${response.headers.get('X-Export-Row-Count')}. Расчёт ${response.headers.get('X-Analysis-Revision')}; данные ${response.headers.get('X-Data-Revision')}; настройки ${response.headers.get('X-Settings-Revision')}; на ${response.headers.get('X-Export-As-Of')}.`;
  } catch (error) {
    $('csv-error').hidden = false;
    $('csv-error').textContent = error.status === 409 ? 'Рабочая версия изменилась. Обновите очередь или повторите экспорт фильтра.' : error.status === 413 ? 'Выборка превышает предел CSV. Сузьте фильтр.' : errorText(error);
    $('csv-status').textContent = 'CSV не скачан. Повторите экспорт.';
    if (error.status === 409) invalidateQueue();
  } finally {
    clearTimeout(timeout); csvBusy = false; button.removeAttribute('aria-busy');
    button.disabled = scope === 'worklist' && (queueStale || !$('worklist-rows').querySelector('.csv-customer:checked'));
  }
}
$('csv-filter').addEventListener('submit',event => {event.preventDefault();downloadCsv('filtered');});
$('worklist-csv').addEventListener('click',() => downloadCsv('worklist'));
addEventListener('focus',async () => {
  if (!queue || queueStale) return;
  try { observeRevision((await api('/customers?limit=1')).data); } catch { invalidateQueue(); }
});
async function preview(mode) {
  if (!current || stale) { message('Обновите рабочую базу перед сравнением. Черновик сохранён.'); return; }
  const body = {expected_revision:revision(current),customer_id:customerId,mode};
  if (mode === 'model') {
    const weights = Object.fromEntries([...$('lab-weights').querySelectorAll('input')].map(input => [input.dataset.feature,Number(input.value)]));
    const medium = Number($('lab-medium').value), high = Number($('lab-high').value);
    if (!(medium > 0 && medium < high && high <= 100) || !Object.values(weights).some(value => value > 0)) {
      $('lab-error').hidden = false; $('lab-error').textContent = 'Хотя бы один вес должен быть больше нуля. Пороги: 0 < средний < высокий ≤ 100.'; return;
    }
    body.settings_overrides = {weights,thresholds:{medium,high}};
  } else body.action = {top_up_cents:Number($('top-up').value),enable_auto_prolong_ids:[...$('action-services').querySelectorAll('input:checked')].map(input => Number(input.value))};
  await operation(async (signal,valid) => {
    message('Сравниваем сценарий с рабочей базой…');
    const result = (await api('/lab/preview',{method:'POST',body:JSON.stringify(body),signal})).data;
    if (!valid()) return;
    const target = $(mode+'-result'), scenario = mode === 'model' ? result.model_scenario : result.action_scenario;
    target.replaceChildren(el('h3',mode === 'model' ? 'Результат изменения модели' : 'Гипотетический результат действия'),el('p',`Сравнение с расчётом ${result.analysis_revision}.`),pairs([
      ['Изменение модельного индекса',result.delta.risk_score ?? 'Недостаточно данных'],['Изменение стоимости под риском',eur(result.delta.monetary_risk_cents)],['Изменение нехватки',eur(result.delta.funding_gap_cents)],['Категория риска изменилась',result.delta.category_changed ? 'Да' : 'Нет']
    ]));
    if (mode === 'action') target.append(el('p',`Предполагаемое пополнение: ${eur(result.effective_action.top_up_cents)}. Включение автопродления: ${result.effective_action.enable_auto_prolong_ids.join(', ') || 'не выбрано'}.`));
    for (const delta of result.delta.features) target.append(el('p',`Услуга ${delta.service_id}, ${titles[delta.feature_id] || delta.feature_id}: значение ${delta.baseline_value ?? 'недоступно'} → ${delta.scenario_value ?? 'недоступно'}; вес ${delta.baseline_weight} → ${delta.scenario_weight}; вклад ${delta.baseline_contribution ?? 'недоступно'} → ${delta.scenario_contribution ?? 'недоступно'}. ${delta.explanation}`));
    if (result.delta.remaining_reasons.length) target.append(el('h3','Оставшиеся причины'),list(result.delta.remaining_reasons.map(reason => reason.explanation)));
    target.append(describeCustomer(scenario));
    offerCaseExport(target,mode === 'model' ? {type:'model',settings_overrides:result.effective_overrides} : {type:'action',action:result.effective_action},result);
    message('Сравнение рассчитано. Рабочие данные не изменены.');
  });
}

function markSensitivityStale() {
  $('sensitivity-result').dataset.state = 'stale';
  $('sensitivity-status').textContent = 'Результат чувствительности устарел. Обновите рабочую базу и запустите проверку снова; черновики сохранены.';
}
function sensitivityTable(title, variants, thresholdMode = false) {
  const wrapper = el('div',undefined,'sensitivity-table-wrap'), table = el('table'), head = el('thead'), header = el('tr'), body = el('tbody');
  table.append(el('caption',title));
  for (const label of ['Вариант','Изменённый параметр','Индекс','Категория','Место','Входит в top-K','Покрытие: оценены / всего / без оценки']) {
    const cell = el('th',label); cell.scope = 'col'; header.append(cell);
  }
  head.append(header); table.append(head,body);
  const levelNames = {low:'Низкий',medium:'Средний',high:'Высокий',unknown:'Нет оценки'};
  for (const variant of variants) {
    const row = el('tr'), parameter = thresholdMode ? (variant.parameter === 'medium' ? 'Средний порог' : 'Высокий порог') : titles[variant.parameter] || variant.parameter;
    for (const text of [variant.id,`${parameter}: ${variant.value}`,variant.risk_score ?? 'Нет оценки',levelNames[variant.risk_level] || variant.risk_level,
      variant.rank ?? 'Нет места',`${variant.in_top_k ? 'Да' : 'Нет'}; K=${variant.top_k}`,
      `${variant.coverage.scored_customers} / ${variant.coverage.total_customers} / ${variant.coverage.unknown_customers}`]) row.append(el('td',text));
    body.append(row);
  }
  wrapper.tabIndex = 0; wrapper.setAttribute('role','region'); wrapper.setAttribute('aria-label',title+' — таблица с горизонтальной прокруткой'); wrapper.append(table);
  return wrapper;
}
function renderSensitivity(data) {
  const target = $('sensitivity-result'), weights = data.weights, thresholds = data.thresholds;
  target.dataset.state = 'complete';
  target.replaceChildren(el('h3','Чувствительность по рабочей модели'),
    el('p',`Клиент ${data.baseline.customer_id}. Расчёт ${data.analysis_revision}, данные ${data.data_revision}, настройки ${data.settings_revision}. Дата ${date(data.as_of)}, горизонт ${data.horizon_days} дней, ${data.model_version}.`),
    pairs([['Рабочий индекс',data.baseline.risk_score ?? 'Нет оценки'],['Рабочая категория',data.baseline.risk_level],['Место в рабочем рейтинге',data.baseline.rank ?? 'Нет места'],['Рабочее K',data.baseline.top_k],
      ['Клиентов в рабочей базе',data.coverage.total_customers],['С оценкой',data.coverage.scored_customers],['Без оценки',data.coverage.unknown_customers]]),
    el('h3','Изменения весов'),pairs([['Знаменатель: уникальных вариантов весов',weights.denominator],
      ['Диапазон индекса',weights.score_min === null ? 'Нет числовых оценок' : `${weights.score_min} – ${weights.score_max}`],
      ['Диапазон мест',weights.rank_min === null ? 'Нет мест с оценкой' : `${weights.rank_min} – ${weights.rank_max}`],
      ['Попаданий в top-K',`${weights.top_k_inclusions} из ${weights.denominator}`]]));
  if (weights.empty || !weights.variants.length) target.append(el('p','Нет уникальных допустимых вариантов весов. Знаменатель равен нулю; диапазон и доля не вычисляются.'));
  else target.append(sensitivityTable('Фактические варианты весов и покрытие каждого расчёта',weights.variants));
  target.append(el('h3','Изменения порогов'),pairs([['Знаменатель: уникальных вариантов порогов',thresholds.denominator],['Категория сохранилась',`${thresholds.category_stable} из ${thresholds.denominator}`]]),
    el('p',`Покрытие пороговых вариантов: ${data.coverage.scored_customers} с оценкой из ${data.coverage.total_customers}; без оценки ${data.coverage.unknown_customers}. Изменение порогов не меняет числовой индекс и порядок рейтинга.`));
  if (thresholds.empty || !thresholds.variants.length) target.append(el('p','Нет уникальных допустимых вариантов порогов. Знаменатель равен нулю; доля стабильности не вычисляется.'));
  else target.append(sensitivityTable('Фактические варианты порогов и категории',thresholds.variants,true));
  const details = el('details'); details.append(el('summary','Эффективные настройки каждого варианта'));
  for (const variant of [...weights.variants,...thresholds.variants]) details.append(el('h3',variant.id),
    pairs([...Object.entries(variant.effective_settings.weights).map(([id,value]) => [titles[id] || id,value]),
      ['Средний порог',variant.effective_settings.thresholds.medium],['Высокий порог',variant.effective_settings.thresholds.high]]));
  target.append(details);
  const reasons = {unchanged_settings:'Настройки совпадают с рабочими',duplicate_settings:'Повтор уже учтённых настроек',thresholds_must_be_ordered:'Нарушен порядок допустимых порогов'};
  target.append(el('h3','Исключённые варианты'),data.excluded_candidates.length ? list(data.excluded_candidates.map(row => `${row.kind === 'weights' ? 'Вес' : 'Порог'} ${titles[row.parameter] || row.parameter}: ${row.value}. ${reasons[row.reason] || row.reason}.`)) : el('p','Исключённых вариантов нет.'));
  $('sensitivity-status').textContent = 'Проверка рассчитана. Знаменатели весов и порогов показаны отдельно. Рабочие данные и черновики не изменены.';
}
async function runSensitivity() {
  $('sensitivity-error').hidden = true;
  if (!current || stale) { markSensitivityStale(); $('refresh-baseline').hidden = !current; return; }
  const captured = current, owner = customerId;
  await operation(async (signal,valid) => {
    sensitivityBusy = true; $('sensitivity-section').setAttribute('aria-busy','true');
    $('sensitivity-result').replaceChildren(); $('sensitivity-result').dataset.state = 'loading';
    $('sensitivity-status').textContent = 'Проверяем варианты весов и порогов по рабочей базе…';
    try {
      const data = (await api('/lab/sensitivity',{method:'POST',signal,body:JSON.stringify({expected_revision:revision(captured),customer_id:owner})})).data;
      if (!valid() || current !== captured || customerId !== owner) return;
      if (!sameRevision(captured,data)) { observeRevision(data); markSensitivityStale(); return; }
      if (stale) { markSensitivityStale(); return; }
      renderSensitivity(data);
    } catch (error) {
      if (!valid()) return;
      $('sensitivity-error').hidden = false;
      $('sensitivity-error').textContent = error.status === 409 ? 'Рабочая версия изменилась. Обновите базу; черновики сохранены.' : error.status === 422 ? 'Варианты не приняты. Проверьте рабочую модель и повторите запрос.' : errorText(error);
      if (error.status === 409) { stale = true; $('refresh-baseline').hidden = false; invalidateQueue(); markSensitivityStale(); }
      else { $('sensitivity-result').dataset.state = 'error'; $('sensitivity-status').textContent = 'Проверка не завершена. Черновики сохранены; повторите запрос.'; }
    } finally {
      if (valid()) { sensitivityBusy = false; $('sensitivity-section').setAttribute('aria-busy','false'); }
    }
  },120000);
}
$('sensitivity-run').addEventListener('click',runSensitivity);

function solverChoices() {
  return Object.fromEntries(['services','switches'].map(name => [name,
    [...$('solver-'+name).querySelectorAll('input:checked')].map(input => input.value)]));
}
function solverGoalFields() {
  const coverage = $('solver-goal').value === 'cover_selected';
  $('solver-coverage').hidden = !coverage;
  $('solver-coverage').disabled = !coverage;
  $('solver-threshold-field').hidden = coverage;
  $('solver-threshold').disabled = coverage;
  $('solver-threshold').required = !coverage;
}
function populateSolver(selection = null) {
  if (!selection) $('solver-form').reset();
  for (const name of ['services','switches']) {
    const target = $('solver-'+name), chosen = selection?.[name] || [];
    target.replaceChildren();
    const services = current.baseline.services.filter(service => service.eligible && service.in_horizon
      && (name === 'services' || (service.input.status === 'active' && !service.input.autoProlong)));
    for (const service of services) {
      const label = el('label',undefined,'lab-check'), input = el('input');
      input.type = 'checkbox'; input.value = service.service_id; input.checked = chosen.includes(String(service.service_id));
      label.append(input,el('span',`${service.input.name} · ID ${service.service_id} · ${eur(service.input.price)} · ${date(service.input.expiresAt)}`));
      target.append(label);
    }
    if (!services.length) target.append(el('p',name === 'services' ? 'Нет учитываемых услуг в горизонте.' : 'Нет допустимых включений автопродления.'));
    if (chosen.some(id => !services.some(service => String(service.service_id) === id))) target.append(el('p','Часть выбранных услуг больше недоступна. Проверьте выбор перед новым поиском.'));
  }
  solverGoalFields(); $('solver-result').replaceChildren(); delete $('solver-result').dataset.status; $('solver-section').hidden = false;
  $('solver-status').textContent = selection ? 'Рабочая база обновлена. Ограничения сохранены; повторите поиск.' : 'Выберите цель и ограничения.';
}
const solverStates = {found:'Минимальное действие найдено',already_satisfied:'Цель уже выполнена без действия',
  unreachable:'Цель недостижима в заданных ограничениях',search_limit:'Достигнут предел поиска; недостижимость не доказана'};
function renderSolver(data) {
  const target = $('solver-result'), certificate = data.certificate;
  target.dataset.status = data.status;
  target.replaceChildren(el('h3',solverStates[data.status]),el('p',`Гипотетический результат. Расчёт ${data.analysis_revision}, данные ${data.data_revision}, настройки ${data.settings_revision}. Дата ${date(data.as_of)}, горизонт ${data.horizon_days} дней, ${data.model_version}.`),
    el('p',data.effective_goal.type === 'score_below' ? `Цель: индекс строго меньше ${data.effective_goal.threshold}.` : `Цель: полное покрытие услуг ${data.effective_goal.service_ids.join(', ')}.`),
    pairs([['Максимальное пополнение, евроценты',data.effective_constraints.max_top_up_cents],['Максимум включений',data.effective_constraints.max_switches],['Проверено кандидатов',data.evaluations],['Поиск завершён полностью',certificate.complete ? 'Да' : 'Нет']]));
  if (data.result) {
    target.append(pairs([['Минимальное пополнение, евроценты',data.top_up_cents],['Включить автопродление, ID',data.enable_auto_prolong_ids.join(', ') || 'Не требуется'],['Изменение модельного индекса',data.delta.risk_score ?? 'Нет оценки'],['Изменение нехватки',eur(data.delta.funding_gap_cents)]]),
      el('h3','Рабочий расчёт'),describeCustomer(data.baseline),el('h3','Гипотетическое действие'),describeCustomer(data.result,true));
    offerCaseExport(target,{type:'solve',goal:data.effective_goal,...data.effective_constraints},data);
  } else {
    target.append(el('p',data.status === 'unreachable' ? 'Полный ограниченный поиск не нашёл действия, выполняющего цель. Увеличьте допустимые ограничения или проверьте оставшиеся причины.' : 'Неполный поиск не даёт сертификата минимальности или невозможности. Попробуйте уменьшить число разрешённых включений.'),
      el('h3','Рабочий расчёт'),describeCustomer(data.baseline));
  }
  target.append(el('h3','Почему действие минимально'),el('p','Порядок выбора: минимальное пополнение, затем меньше включений автопродления, затем наименьший список ID. Для покрытия услуги переключения не создают средств, поэтому выбрано ноль включений.'));
  const minima = el('details'); minima.append(el('summary',`Минимумы проверенных наборов: ${certificate.subset_minima.length}`));
  if (certificate.subset_minima.length) minima.append(list(certificate.subset_minima.map(row => `Включения: ${row.enable_auto_prolong_ids.join(', ') || 'нет'}; минимальное пополнение: ${row.top_up_cents === null ? 'цель не достигнута в пределах суммы' : row.top_up_cents+' евроцентов'}.`)));
  else minima.append(el('p',data.status === 'already_satisfied' ? 'Исходный расчёт уже выполняет цель.' : 'Завершённых проверок наборов нет.'));
  target.append(minima);
  if (data.top_up_cents > 0 && certificate.one_cent_less_result) {
    const button = el('button','Показать проверку на один цент меньше','ds-button ds-button--secondary'), proof = el('div');
    button.type = 'button'; button.id = 'solver-one-cent'; proof.id = 'solver-one-cent-result'; proof.hidden = true;
    button.setAttribute('aria-expanded','false'); button.setAttribute('aria-controls',proof.id);
    button.addEventListener('click',() => {
      proof.hidden = !proof.hidden; button.setAttribute('aria-expanded',String(!proof.hidden));
      proof.replaceChildren(el('p',`При пополнении ${data.top_up_cents-1} евроцентов и тех же включениях автопродления цель ${certificate.one_cent_less_satisfies ? 'выполнена' : 'не выполнена'}. Проверка рассчитана сервером на той же рабочей базе.`),
        pairs([['Индекс риска',score(certificate.one_cent_less_result)],['Нехватка средств',eur(certificate.one_cent_less_result.funding_gap_cents)]]));
    });
    target.append(button,proof);
  } else target.append(el('p','Проверка на цент меньше не применяется: положительного найденного пополнения нет.'));
  $('solver-status').textContent = solverStates[data.status]+'. Рабочие данные не изменены.';
}
async function solveAction() {
  if (!current || stale) { message('Обновите рабочую базу перед поиском. Черновик сохранён.'); return; }
  if (!$('solver-form').reportValidity()) return;
  const choices = solverChoices(), maxSwitches = Number($('solver-max-switches').value);
  if (choices.switches.length > 8 || maxSwitches > choices.switches.length
      || ($('solver-goal').value === 'score_below' && !(Number($('solver-threshold').value) > 0))
      || ($('solver-goal').value === 'cover_selected' && !choices.services.length)) {
    $('solver-status').textContent = 'Для покрытия выберите услугу; порог индекса должен быть больше нуля. Разрешений — не больше 8, максимум включений — не больше числа разрешений.'; return;
  }
  const body = {expected_revision:revision(current),customer_id:customerId,
    goal:$('solver-goal').value === 'cover_selected' ? {type:'cover_selected',service_ids:choices.services.map(Number)} : {type:'score_below',threshold:Number($('solver-threshold').value)},
    max_top_up_cents:Number($('solver-max-cents').value),allowed_enable_auto_prolong_ids:choices.switches.map(Number),max_switches:maxSwitches};
  await operation(async (signal,valid) => {
    $('solver-result').replaceChildren(); $('solver-status').textContent = 'Ищем минимальное действие…';
    let data;
    try { data = (await api('/lab/solve',{method:'POST',signal,body:JSON.stringify(body)})).data; }
    catch (error) {
      if (valid()) $('solver-status').textContent = error.status === 409
        ? 'Рабочая база изменилась. Черновик поиска сохранён; обновите базу и повторите поиск.'
        : error.status === 422 ? 'Поиск не принят. Проверьте цель, услуги, сумму, разрешения и ограничения. Черновик сохранён.'
        : 'Поиск не завершён. Черновик сохранён; повторите запрос.';
      throw error;
    }
    if (!valid()) return;
    observeRevision(data); renderSolver(data);
  },120000);
}
$('solver-form').addEventListener('submit',event => {event.preventDefault();solveAction();});
$('solver-form').addEventListener('input',() => {
  cancel(); $('lab-content').setAttribute('aria-busy','false'); $('solver-result').replaceChildren();
  delete $('solver-result').dataset.status; solverGoalFields(); $('solver-status').textContent = 'Ограничения изменены. Повторите поиск.';
});
$('solver-reset').addEventListener('click',() => {cancel();if(current)populateSolver();});
addEventListener('pagehide',() => {$('solver-result').replaceChildren();$('solver-section').hidden = true;});

let sourcePreview = null, sourceFile = null, sourceGeneration = 0, sourceController = null, sourceExpiry = null;
const sourceEntities = {account:'Клиенты',service:'Услуги',transaction:'Операции'};
const sourceKinds = {added:'Добавлена',removed:'Отсутствует в новом снимке',changed:'Изменена'};
function invalidateSourcePreview(text) {
  sourcePreview = null; sourceFile = null; clearTimeout(sourceExpiry); sourceExpiry = null;
  $('source-confirm-ack').checked = false; $('source-confirm').disabled = true; $('source-confirm-form').hidden = true;
  if (text) $('source-status').textContent = text;
}
function sourceConfirmationBusy(value) {
  for (const id of ['source-file','source-reject','source-confirm-ack']) $(id).disabled = value;
  $('source-preview-form').querySelector('button[type="submit"]').disabled = value;
}
function cancelSource() { sourceGeneration++; sourceController?.abort(); sourceController = null; sourceConfirmationBusy(false); $('source-section').setAttribute('aria-busy','false'); }
function clearSource() {
  cancelSource(); invalidateSourcePreview(); $('source-file').value = ''; $('source-file-info').textContent = 'Файл не выбран.';
  $('source-result').replaceChildren(); $('source-error').hidden = true;
  $('source-status').textContent = 'Предпросмотр отклонён и очищен в этой вкладке. Рабочий снимок не заменён; серверный токен истечёт автоматически.';
}
function sourceApplicable() {
  return sourcePreview && $('source-file').files[0] === sourceFile && Date.parse(sourcePreview.expires_at) > Date.now();
}
async function sourceOperation(fn, confirming = false) {
  cancelSource(); const token = sourceGeneration, active = new AbortController(); sourceController = active;
  const timeout = setTimeout(() => active.abort(),60000);
  $('source-error').hidden = true; $('source-section').setAttribute('aria-busy','true'); $('source-confirm').disabled = true;
  if (confirming) sourceConfirmationBusy(true);
  try { await fn(active.signal,() => token === sourceGeneration); }
  catch (error) {
    if (token !== sourceGeneration) return;
    invalidateSourcePreview(); $('source-error').hidden = false;
    const status = error.status;
    $('source-error').textContent = status === 409 ? 'Рабочая версия, срок или содержимое предпросмотра изменились. Ничего не подтверждено этим ответом; создайте новый предпросмотр.'
      : status === 413 ? 'Запрос превышает лимит снимка на сервере. Выберите меньший полный файл.'
      : status === 400 ? 'Файл должен содержать корректный JSON полного снимка.'
      : status === 422 ? 'Снимок не принят: проверьте поля, уникальные ID, связи и допустимые суммы.' : errorText(error);
    $('source-status').textContent = 'Операция не завершена. Файл сохранён для нового предпросмотра; проверьте текущую рабочую версию перед повтором.';
    if (status === 409 && error.payload?.error?.current_revision) observeRevision(error.payload.error.current_revision);
  } finally {
    clearTimeout(timeout);
    if (token === sourceGeneration) { sourceController = null; sourceConfirmationBusy(false); $('source-section').setAttribute('aria-busy','false'); }
  }
}
function sourceSummary(value) {
  return pairs([['Клиентов',value.customer_count],['С индексом',value.scored_customer_count],['Без индекса',value.unknown_customer_count],
    ['Стоимость под риском',eur(value.monetary_risk_cents)],['Клиентов с денежной оценкой',value.monetary_coverage_evaluated],['Без денежной оценки',value.monetary_coverage_unknown],
    ['Ближайшие продления',eur(value.due_services_price_cents)],['Нехватка средств',eur(value.funding_gap_cents)],
    ['Категории',value.risk_counts.map(([level,count]) => `${level}: ${count}`).join('; ')]]);
}
function sourceFieldValue(value, present) {
  if (!present) return 'Поле отсутствует';
  return value === null ? 'null (явное значение)' : JSON.stringify(value);
}
function renderSourcePreview(data) {
  const target = $('source-result'), diff = data.diff;
  target.replaceChildren(el('h3','Сравнение полного снимка без записи'),pairs([
    ['Предпросмотр',data.preview_id],['Контрольная сумма нового снимка',data.candidate_checksum],['Действует до',date(data.expires_at)],
    ['Рабочая версия',`${data.analysis_revision} · данные ${data.data_revision} · настройки ${data.settings_revision}`],
    ['Единая дата расчёта',date(data.as_of)],['Горизонт, дней',data.horizon_days],['Модель',data.model_version]]));
  for (const row of diff.entity_counts) target.append(el('h3',sourceEntities[row.entity]),pairs([
    ['В рабочем снимке',row.before],['В новом снимке',row.after],['Добавлено',row.added],['Отсутствует',row.removed],['Изменено',row.changed]]));
  target.append(el('h3','Рабочий расчёт'),sourceSummary(diff.before),el('h3','Новый снимок при той же модели и дате'),sourceSummary(diff.after),el('h3','Предупреждения'));
  target.append(diff.warnings.length ? list(diff.warnings.map(code => code === 'source_record_absence_is_input_difference_only' ? 'Отсутствие записи — различие входных данных; оно не доказывает уход клиента.' : code)) : el('p','Предупреждений нет.'));
  const records = el('details'); records.append(el('summary',`Изменения исходных записей: ${diff.record_changes.length}`));
  for (const row of diff.record_changes) records.append(el('h3',`${sourceEntities[row.entity]} · ID ${row.record_id} · ${sourceKinds[row.kind]}`),
    list(row.fields.map(field => `${field.name}: ${sourceFieldValue(field.before,field.before_present)} → ${sourceFieldValue(field.after,field.after_present)}`)));
  if (!diff.record_changes.length) records.append(el('p','Исходные записи совпадают. Подтверждение не изменит данные.'));
  target.append(records);
  const results = el('details'); results.append(el('summary',`Изменение расчётов клиентов: ${diff.customer_results.length}`));
  for (const row of diff.customer_results) results.append(el('h3',`Клиент ${row.customer_id}`),pairs([
    ['Наличие записи',row.source_change === 'present' ? 'В обоих снимках' : sourceKinds[row.source_change]],
    ['Индекс: рабочий → новый',`${row.before_score ?? 'Нет оценки'} → ${row.after_score ?? 'Нет оценки'}`],['Изменение модельного индекса',row.score_delta ?? 'Не сравнивается'],
    ['Категория: рабочая → новая',`${row.before_level ?? 'Нет'} → ${row.after_level ?? 'Нет'}`],['Место: рабочее → новое',`${row.before_rank ?? 'Нет'} → ${row.after_rank ?? 'Нет'}`],
    ['Стоимость под риском: рабочая → новая',`${eur(row.before_money_cents)} → ${eur(row.after_money_cents)}`],['Изменение стоимости под риском',eur(row.money_delta_cents)],
    ['Стоимость продлений: рабочая → новая',`${eur(row.before_due_price_cents)} → ${eur(row.after_due_price_cents)}`],['Нехватка: рабочая → новая',`${eur(row.before_funding_gap_cents)} → ${eur(row.after_funding_gap_cents)}`]]));
  if (!diff.customer_results.length) results.append(el('p','Клиентов нет в обоих снимках.'));
  target.append(results);
}
async function previewSource() {
  const file = $('source-file').files[0]; if (!file) return;
  invalidateSourcePreview(); $('source-result').replaceChildren();
  await sourceOperation(async (signal,valid) => {
    $('source-status').textContent = 'Проверяем файл и рассчитываем различия без записи…';
    const reject = status => { throw {status}; };
    if (file.size > 10*1024*1024) reject(413);
    let text;
    try { text = new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await file.arrayBuffer()); JSON.parse(text); }
    catch { reject(400); }
    if (!valid()) return;
    const metadata = (await api('/customers?limit=1',{signal})).data;
    if (!valid()) return;
    observeRevision(metadata);
    const body = `{"expected_revision":${JSON.stringify(revision(metadata))},"candidate_snapshot":${text}}`;
    if (new TextEncoder().encode(body).length > 10*1024*1024) reject(413);
    const data = (await api('/source/snapshot/preview',{method:'POST',signal,body})).data;
    if (!valid() || $('source-file').files[0] !== file) return;
    observeRevision(data); sourcePreview = data; sourceFile = file; renderSourcePreview(data);
    $('source-confirm-form').hidden = false; $('source-confirm-ack').checked = false;
    $('source-status').textContent = 'Предпросмотр готов. Рабочие данные не изменены. Для полной замены проверьте различия и отдельное подтверждение.';
    sourceExpiry = setTimeout(() => invalidateSourcePreview('Срок предпросмотра истёк. Создайте новый по выбранному файлу.'),Math.max(0,Date.parse(data.expires_at)-Date.now()));
  });
}
async function confirmSource() {
  if (!sourceApplicable() || !$('source-confirm-ack').checked) { invalidateSourcePreview('Подтверждение недоступно. Создайте новый предпросмотр выбранного файла.'); return; }
  const captured = sourcePreview;
  await sourceOperation(async (signal,valid) => {
    $('source-status').textContent = 'Подтверждаем полную замену проверенного снимка…';
    const expected = revision(captured), etag = `"lab-${expected.analysis_revision}-d-${expected.data_revision}-s-${expected.settings_revision}"`;
    const result = (await api('/source/snapshot/preview/'+encodeURIComponent(captured.preview_id)+'/confirm',{
      method:'POST',signal,headers:{'If-Match':etag},body:JSON.stringify({expected_revision:expected,candidate_checksum:captured.candidate_checksum})})).data;
    if (!valid()) return;
    invalidateSourcePreview(); observeRevision(result);
    $('source-result').append(el('h3',result.changed ? 'Полный снимок заменён' : 'Снимок совпал с рабочим: запись не потребовалась'),
      pairs([['Текущая версия',`${result.analysis_revision} · данные ${result.data_revision} · настройки ${result.settings_revision}`],['Контрольная сумма',result.checksum],['Клиентов',result.counts.accounts],['Услуг',result.counts.services],['Операций',result.counts.transactions]]));
    $('source-status').textContent = result.changed ? 'Полная замена выполнена. Обновите рабочий расчёт и очередь; черновики сохранены.' : 'Подтверждён совпадающий снимок. Рабочие данные и версии не изменены.';
  },true);
}
$('source-preview-form').addEventListener('submit',event => {event.preventDefault();previewSource();});
$('source-confirm-form').addEventListener('submit',event => {event.preventDefault();confirmSource();});
$('source-confirm-ack').addEventListener('change',() => {$('source-confirm').disabled = !sourceApplicable() || !$('source-confirm-ack').checked;});
$('source-file').addEventListener('change',() => {
  cancelSource(); invalidateSourcePreview(); $('source-result').replaceChildren(); $('source-error').hidden = true;
  const file = $('source-file').files[0]; $('source-file-info').textContent = file ? `${file.name} · ${file.size} байт` : 'Файл не выбран.';
  $('source-status').textContent = 'Файл изменён. Создайте новый предпросмотр; прежнее подтверждение недоступно.';
});
$('source-reject').addEventListener('click',clearSource);
$('logout').addEventListener('click',clearSource,{capture:true});
addEventListener('pagehide',clearSource);

const caseErrorTitles = {
  case_too_large:'Паспорт превышает лимит 64 KiB.', request_too_large:'Запрос с паспортом превышает лимит 64 KiB.',
  invalid_case_schema:'Структура паспорта повреждена или содержит недопустимые поля.',
  case_checksum_mismatch:'Контрольная сумма не совпала. Содержимое паспорта изменено.',
  unsupported_case_version:'Версия формата паспорта не поддерживается.',
  unsupported_case_engine:'Версия модели паспорта не поддерживается.',
  unsupported_case_features:'Версии признаков паспорта не поддерживаются.',
  case_result_mismatch:'Повторный расчёт не совпал с результатом паспорта.',
  case_service_limit:'Паспорт допускает не больше 100 услуг.', case_switch_limit:'Паспорт допускает не больше 8 переключений автопродления.',
  service_not_allowed:'Услуга в сохранённом сценарии недоступна.',
};
let caseGeneration = 0, caseController = null;
function clearCase() {
  caseGeneration++; caseController?.abort(); caseController = null;
  $('case-file').value = ''; $('case-result').replaceChildren();
  $('case-error').hidden = true; $('case-error').textContent = '';
  $('case-section').setAttribute('aria-busy','false');
  $('case-status').textContent = 'Паспорт не загружен.';
}
async function caseOperation(fn) {
  const token = ++caseGeneration; caseController?.abort();
  const active = new AbortController(); caseController = active;
  const timeout = setTimeout(() => active.abort(),15000);
  $('case-error').hidden = true; $('case-error').textContent = '';
  $('case-result').replaceChildren(); $('case-section').setAttribute('aria-busy','true');
  try { await fn(active.signal,() => token === caseGeneration); }
  catch (error) {
    if (token !== caseGeneration) return;
    if (error.status === 401) clearCase();
    if (error.status === 409) { invalidateQueue(); if(current) { stale = true; $('refresh-baseline').hidden = false; } }
    $('case-error').hidden = false;
    $('case-error').textContent = error.status === 409
      ? 'Рабочая версия изменилась во время запроса. Повторите загрузку паспорта; для нового экспорта обновите базу и пересчитайте сценарий.'
      : caseErrorTitles[error.payload?.error?.code] || errorText(error);
    $('case-status').textContent = 'Паспорт не обработан. Рабочие данные не изменены.';
  } finally {
    clearTimeout(timeout);
    if (token === caseGeneration) { caseController = null; $('case-section').setAttribute('aria-busy','false'); }
  }
}
function offerCaseExport(target, scenario, resultRevision) {
  if (!['model','action','solve'].includes(scenario.type)) throw new Error('Unsupported case operation');
  // Capture calculated inputs and tuple; editing the form removes this button.
  const captured = structuredClone(scenario), expected = revision(resultRevision), owner = customerId;
  const button = el('button','Сохранить паспорт решения','ds-button ds-button--secondary');
  button.type = 'button'; button.classList.add('case-export');
  button.addEventListener('click',() => caseOperation(async (signal,valid) => {
    $('case-status').textContent = 'Готовим паспорт рассчитанного сценария…';
    const data = (await api('/lab/cases/export',{method:'POST',signal,body:JSON.stringify({expected_revision:expected,customer_id:owner,operation:captured})})).data;
    if (!valid() || !button.isConnected) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(data)],{type:'application/json'}));
    const link = el('a'); link.href = url; link.download = 'decision-case-v1.json';
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url),1000);
    $('case-status').textContent = 'Паспорт сохранён в файл. Имена и ID заменены синтетическими; точные суммы и даты сохранены.';
  }));
  target.append(button);
}
function caseSummary(value) {
  if (!value) return el('p','Результат сценария отсутствует.');
  return pairs([['Индекс риска',value.risk_score === null ? 'Нет оценки' : value.risk_score],
    ['Категория',value.risk_level],['Стоимость под риском, евроценты',value.monetary_risk_cents ?? 'Нет оценки'],
    ['Нехватка, евроценты',value.funding_gap_cents],['Ближайшие продления, евроценты',value.due_services_price_cents]]);
}
async function replayCase() {
  const file = $('case-file').files[0]; if (!file) return;
  await caseOperation(async (signal,valid) => {
    $('case-status').textContent = 'Проверяем паспорт и повторяем расчёт…';
    const reject = code => { throw {status:422,payload:{error:{code}}}; };
    if (file.size > 65536) reject('case_too_large');
    let document;
    try { document = new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await file.arrayBuffer()); }
    catch { reject('invalid_case_schema'); }
    if (!valid()) return;
    // Keep the original text: parsing here would erase duplicate JSON keys.
    const metadata = (await api('/customers?limit=1',{signal})).data;
    if (!valid()) return;
    observeRevision(metadata);
    const body = JSON.stringify({expected_revision:revision(metadata),case_document:document});
    if (new TextEncoder().encode(body).length > 65536) reject('request_too_large');
    const data = (await api('/lab/cases/replay',{method:'POST',signal,body})).data;
    if (!valid()) return;
    const calculated = data.calculated, target = $('case-result');
    target.replaceChildren(el('h3','Повторный расчёт в песочнице'),
      el('p','Настоящий engine заново рассчитал входы из файла. Результат совпал с сохранённым.'),
      pairs([['Дата паспорта',date(data.case_as_of)],['Модель паспорта',data.case_engine_version],['Горизонт паспорта, дней',data.case_horizon_days],['Контрольная сумма',data.checksum]]),
      el('h3','Исходный расчёт паспорта'),caseSummary(calculated.baseline),el('h3','Сценарий паспорта'),caseSummary(calculated.scenario));
    if (calculated.type === 'solve') target.append(pairs([
      ['Статус поиска',calculated.status],['Минимальное пополнение, евроценты',calculated.top_up_cents ?? 'Не найдено'],
      ['Включить автопродление, синтетические ID',calculated.enable_auto_prolong_ids.join(', ') || 'Нет'],
      ['Сертификат поиска полный',calculated.certificate_complete ? 'Да' : 'Нет'],
      ['На цент меньше достигает цели',calculated.one_cent_less_satisfies === null ? 'Не проверяется' : calculated.one_cent_less_satisfies ? 'Да' : 'Нет']
    ]));
    for (const row of calculated.scenario?.services || []) target.append(el('p',`Синтетическая услуга ${row.service_id}: индекс ${row.risk_score ?? 'неизвестен'}, покрыто ${row.covered_cents ?? 'неизвестно'} евроцентов, нехватка ${row.funding_gap_cents ?? 'неизвестна'} евроцентов.`));
    target.append(el('p','Ручной разбор исходных полей и вкладов доступен в рабочем расчёте выбранного клиента. Это отдельная проверка арифметики; машинный повтор использует тот же engine.'));
    $('case-status').textContent = 'Расчёт паспорта воспроизведён. Рабочие данные и настройки не изменены.';
  });
}
$('case-form').addEventListener('submit',event => { event.preventDefault(); replayCase(); });
$('case-clear').addEventListener('click',clearCase);
$('case-file').addEventListener('change',() => {
  caseGeneration++; caseController?.abort(); caseController = null;
  $('case-result').replaceChildren(); $('case-error').hidden = true;
  $('case-section').setAttribute('aria-busy','false');
  $('case-status').textContent = 'Выбран новый файл. Запустите повторный расчёт.';
});
$('logout').addEventListener('click',clearCase,{capture:true});
addEventListener('pagehide',clearCase);

$('customer-form').addEventListener('submit',event => {event.preventDefault();loadBaseline();});
$('customer-id').addEventListener('input',() => {cancel();$('lab-content').setAttribute('aria-busy','false');});
$('refresh-baseline').addEventListener('click',() => loadBaseline(true));
for (const mode of ['model','action']) {
  $(mode+'-form').addEventListener('submit',event => {event.preventDefault();preview(mode);});
  $(mode+'-form').addEventListener('input',() => {cancel();$('lab-content').setAttribute('aria-busy','false');$(mode+'-result').replaceChildren();message('Черновик изменён. Запустите сравнение.');});
}
$('reset-model').addEventListener('click',() => {cancel();if(current)populateModel();$('model-result').replaceChildren();message('Черновик модели сброшен к загруженной рабочей модели.');});
$('reset-action').addEventListener('click',() => {cancel();$('top-up').value = '0';if(current)populateActions();$('action-result').replaceChildren();message('Черновик действия сброшен.');});
addEventListener('pagehide',() => {cancel();queueGeneration++;queueController?.abort();queue = null;selectedCustomer = null;current = null;$('worklist-rows').replaceChildren();$('lab-content').replaceChildren();});
try {
  await startShell('lab');
  const id = new URLSearchParams(location.search).get('customer_id');
  if (id) { $('customer-id').value = id; if ($('customer-id').checkValidity()) await loadBaseline(); }
} catch (error) { fail(error); }
