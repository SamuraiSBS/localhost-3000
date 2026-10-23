import { startShell, api, el, errorText } from './shell.js';
const form=document.querySelector('#settings-form');
function announce(text){if(status.textContent!==text)status.textContent=text;}
const status=document.querySelector('#settings-status');
const general=document.querySelector('#settings-error');
const reload=document.querySelector('#reload-settings');
const save=document.querySelector('#save-settings');
let saved=null, etag=null, remote=null, saving=false, importing=false;
export let settingsDirty=false;
function clearErrors(){general.hidden=true;for(const input of form.querySelectorAll('input')){input.removeAttribute('aria-invalid');const error=document.getElementById(input.id+'-error');if(error)error.hidden=true;}}
function fieldError(id,text){const input=document.getElementById(id);input.setAttribute('aria-invalid','true');const error=document.getElementById(id+'-error');error.textContent=text;error.hidden=false;}
function populate(data){
  for(const feature of data.features){
    const wrapper=el('div',undefined,'ds-field feature-field');const id='weight-'+feature.id;
    const existing=document.getElementById(id);
    if(existing){existing.value=data.weights[feature.id];document.getElementById(id+'-hint').textContent=feature.description+` Диапазон ${feature.min_weight}–${feature.max_weight}. Сохранено: ${data.weights[feature.id]}.`;continue;}
    const label=el('label',feature.title+' — вес, баллов');label.htmlFor=id;
    const hint=el('p',feature.description+` Диапазон ${feature.min_weight}–${feature.max_weight}. Сохранено: ${data.weights[feature.id]}.`,'ds-hint');hint.id=id+'-hint';
    const input=el('input',undefined,'ds-input');input.type='number';input.id=id;input.name=feature.id;input.min=feature.min_weight;input.max=feature.max_weight;input.step='any';input.required=true;input.value=data.weights[feature.id];input.setAttribute('aria-describedby',id+'-hint '+id+'-error');
    const error=el('p',undefined,'ds-error');error.id=id+'-error';error.hidden=true;wrapper.append(label,hint,input,error);document.querySelector('#feature-fields').append(wrapper);
  }
  document.querySelector('#horizon').value=data.horizon_days;document.querySelector('#medium').value=data.thresholds.medium;document.querySelector('#high').value=data.thresholds.high;
  form.hidden=false;settingsDirty=false;clearErrors();
}
function savedStatus(data,prefix='Сохранённая конфигурация'){announce(`${prefix}. Версия настроек: ${data.settings_revision}; модель: ${data.model_version}`+(data.analysis_revision?`; расчёт: ${data.analysis_revision}.`:'.'));}
async function readSettings(background=false,signal){
  if(!background){announce('Загружаем настройки…');form.setAttribute('aria-busy','true');}
  try{
    const result=await api('/model/settings',{signal});
    if(stopped)return;
    if(settingsDirty&&saved){if(result.data.settings_revision!==saved.settings_revision){remote=result;announce('Настройки изменены в другой вкладке. Ваш черновик сохранён; отмените его или загрузите свежие настройки.');reload.hidden=false;}return;}
    const changed=!saved||saved.settings_revision!==result.data.settings_revision;
    saved=result.data;etag=result.etag;remote=null;if(changed)populate(saved);savedStatus(saved);reload.hidden=true;
  }
  catch(error){if(error.status===401){stopped=true;saved=null;remote=null;settingsDirty=false;return;}announce((saved?'Настройки и черновик оставлены в форме. ':'')+errorText(error));reload.hidden=false;throw error;}
  finally{form.setAttribute('aria-busy','false');}
}
form.addEventListener('input',()=>{settingsDirty=true;});
form.addEventListener('submit',async event=>{
  event.preventDefault();if(running||saving||importing||!saved){announce('Дождитесь завершения текущего запроса и повторите сохранение. Черновик сохранён.');return;}saving=true;clearTimeout(timer);clearErrors();
  const weights={};let valid=true;
  for(const feature of saved.features){const input=document.getElementById('weight-'+feature.id);const value=Number(input.value);weights[feature.id]=value;if(input.value.trim()===''||!Number.isFinite(value)||value<feature.min_weight||value>feature.max_weight){fieldError(input.id,`Введите число от ${feature.min_weight} до ${feature.max_weight}.`);valid=false;}}
  if(!Object.values(weights).some(value=>value>0)){for(const feature of saved.features)fieldError('weight-'+feature.id,'Хотя бы один вес должен быть больше нуля.');valid=false;}
  const horizon=Number(document.querySelector('#horizon').value), medium=Number(document.querySelector('#medium').value),high=Number(document.querySelector('#high').value);
  if(!Number.isInteger(horizon)||horizon<1||horizon>90){fieldError('horizon','Введите целое число дней от 1 до 90.');valid=false;}
  if(!(medium>0&&medium<high&&high<=100)){fieldError('medium','Нужны пороги: 0 < средний < высокий ≤ 100.');fieldError('high','Высокий порог должен быть больше среднего и не выше 100.');valid=false;}
  if(!valid){saving=false;schedule();form.querySelector('[aria-invalid="true"]')?.focus();return;}
  form.querySelectorAll('input').forEach(input=>input.disabled=true);
  save.disabled=true;save.textContent='Сохраняем и пересчитываем…';save.setAttribute('aria-busy','true');document.querySelector('#cancel-settings').disabled=true;
  try{
    const result=await api('/model/settings',{method:'PATCH',headers:{'If-Match':etag},body:JSON.stringify({weights,horizon_days:horizon,thresholds:{medium,high}})});
    saved=result.data;etag=result.etag;populate(saved);savedStatus(saved,'Сохранено и пересчитано');reload.hidden=true;
    // The saved values and actual analysis revision above come from the PATCH response.
  }catch(error){
    general.hidden=false;
    if(error.status===409){general.textContent='Настройки уже изменились. Ваши правки сохранены в форме. Загрузите свежие настройки, затем повторите нужную правку.';reload.hidden=false;}
    else if(error.status===422){general.textContent='Сервер не принял настройки. Проверьте диапазоны, веса и порядок порогов. Введённые значения сохранены.';}
    else general.textContent=errorText(error);
  }finally{saving=false;form.querySelectorAll('input').forEach(input=>input.disabled=false);schedule();save.disabled=false;save.textContent='Сохранить и пересчитать';save.removeAttribute('aria-busy');document.querySelector('#cancel-settings').disabled=false;}
});
document.querySelector('#cancel-settings').addEventListener('click',()=>{if(remote){saved=remote.data;etag=remote.etag;remote=null;}if(saved){populate(saved);savedStatus(saved,'Изменения отменены');reload.hidden=true;}});
reload.addEventListener('click',()=>{if(settingsDirty&&!confirm('Заменить ваш несохранённый черновик актуальными настройками сервера?'))return;settingsDirty=false;refresh();});
let timer, delay=2000, running=false, pending=false, stopped=false, ready=false, controller;
function schedule(){clearTimeout(timer);if(!stopped&&!document.hidden)timer=setTimeout(()=>refresh(true),delay);}
async function refresh(background=false){
  if(!ready||stopped||saving||importing||document.hidden&&background)return;
  if(running){if(!background)pending=true;return;}
  running=true;controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),10000);
  try{await readSettings(background,controller.signal);delay=2000;}catch{delay=Math.min(30000,delay*2);}
  finally{clearTimeout(timeout);running=false;if(pending){pending=false;refresh();}else schedule();}
}
export function loadSettings(){return refresh();}
addEventListener('focus',()=>refresh(true));addEventListener('online',()=>refresh(true));
document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(timer);else refresh(true);});
addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);controller?.abort();saved=null;remote=null;});
const importForm=document.querySelector('#snapshot-form');
const fileInput=document.querySelector('#snapshot-file');
const confirmation=document.querySelector('#snapshot-confirm');
const importStatus=document.querySelector('#import-status');
const importErrors=document.querySelector('#import-errors');
const importWarnings=document.querySelector('#import-warnings');
fileInput.addEventListener('change',()=>{confirmation.checked=false;importErrors.replaceChildren();importStatus.textContent=fileInput.files[0]?`Выбран файл: ${fileInput.files[0].name}, ${fileInput.files[0].size} байт. Проверьте его и отдельно подтвердите полную замену.`:'';});
importForm.addEventListener('submit',async event=>{
  event.preventDefault();if(importing||saving||running){importStatus.textContent='Дождитесь завершения текущего запроса и повторите импорт. Выбранный файл сохранён.';return;}
  const file=fileInput.files[0];
  if(!file||!confirmation.checked){importStatus.textContent='Выберите файл и подтвердите полную замену.';return;}
  importing=true;clearTimeout(timer);importErrors.replaceChildren();importErrors.hidden=true;importWarnings.replaceChildren();
  const button=document.querySelector('#import-snapshot');button.disabled=true;fileInput.disabled=true;confirmation.disabled=true;importForm.setAttribute('aria-busy','true');
  importStatus.textContent='Проверяем, заменяем снимок и пересчитываем…';
  try{
    if(file.size>10*1024*1024){const error=new Error();error.status=413;throw error;}
    const content=await file.text();
    try{JSON.parse(content);}catch{const error=new Error('invalid_json');throw error;}
    const result=await api('/source/snapshot',{method:'PUT',body:content});
    const data=result.data;
    importStatus.textContent=(data.changed?'Снимок заменён и пересчитан.':'Этот снимок уже активен. Повторный импорт не изменил данные.')+` Данные: ${data.data_revision}; расчёт: ${data.analysis_revision}. Получен: ${new Date(data.received_at).toLocaleString('ru-RU', {timeZone:'UTC'})} UTC. Клиентов: ${data.counts.accounts}; услуг: ${data.counts.services}; транзакций: ${data.counts.transactions}. Открытый рейтинг обновится автоматически.`;
    for(const warning of data.warnings||[])importWarnings.append(el('li',`Предупреждение источника: ${warning.code}; случаев: ${warning.count}.`));
    if(!data.warnings?.length)importWarnings.append(el('li','Предупреждений качества данных нет.'));
  }catch(error){
    if(error.status===401){stopped=true;return;}
    const reason=error.message==='invalid_json'?'Некорректный JSON. Проверьте синтаксис файла.':error.status===413?'Файл превышает допустимые 10 МБ.':error.status===422?'Снимок не прошёл проверку полей.':errorText(error);
    importStatus.textContent=error.status===0||error.status>=500
      ? 'Не удалось подтвердить результат импорта. Проверьте связь и рейтинг: сервер мог принять снимок. Выбранный файл сохранён; повтор того же снимка не создаёт новую версию данных.'
      : 'Снимок не заменён. Последний согласованный результат остаётся активным. Выбранный файл сохранён.';
    importErrors.hidden=false;importErrors.append(el('p',reason,'ds-error'));
    for(const item of error.payload?.error?.details||[])importErrors.append(el('p',`${item.path}: ${item.reason}`,'ds-error'));
  }finally{
    importing=false;button.disabled=false;fileInput.disabled=false;confirmation.disabled=false;confirmation.checked=false;importForm.setAttribute('aria-busy','false');schedule();
  }
});
try{await startShell('settings');ready=true;await loadSettings();}catch(error){announce(errorText(error));}
