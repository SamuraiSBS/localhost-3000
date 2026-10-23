import { startShell, api, el, errorText } from './shell.js';
const form=document.querySelector('#settings-form');
const status=document.querySelector('#settings-status');
const general=document.querySelector('#settings-error');
const reload=document.querySelector('#reload-settings');
const save=document.querySelector('#save-settings');
let saved=null, etag=null;
export let settingsDirty=false;
function clearErrors(){general.hidden=true;for(const input of form.querySelectorAll('input')){input.removeAttribute('aria-invalid');const error=document.getElementById(input.id+'-error');if(error)error.hidden=true;}}
function fieldError(id,text){const input=document.getElementById(id);input.setAttribute('aria-invalid','true');const error=document.getElementById(id+'-error');error.textContent=text;error.hidden=false;}
function populate(data){
  document.querySelector('#feature-fields').replaceChildren();
  for(const feature of data.features){
    const wrapper=el('div',undefined,'ds-field feature-field');const id='weight-'+feature.id;
    const label=el('label',feature.title+' — вес, баллов');label.htmlFor=id;
    const hint=el('p',feature.description+` Диапазон ${feature.min_weight}–${feature.max_weight}. Сохранено: ${data.weights[feature.id]}.`,'ds-hint');hint.id=id+'-hint';
    const input=el('input',undefined,'ds-input');input.type='number';input.id=id;input.name=feature.id;input.min=feature.min_weight;input.max=feature.max_weight;input.step='any';input.required=true;input.value=data.weights[feature.id];input.setAttribute('aria-describedby',id+'-hint '+id+'-error');
    const error=el('p',undefined,'ds-error');error.id=id+'-error';error.hidden=true;wrapper.append(label,hint,input,error);document.querySelector('#feature-fields').append(wrapper);
  }
  document.querySelector('#horizon').value=data.horizon_days;document.querySelector('#medium').value=data.thresholds.medium;document.querySelector('#high').value=data.thresholds.high;
  form.hidden=false;settingsDirty=false;clearErrors();
}
function savedStatus(data,prefix='Сохранённая конфигурация'){status.textContent=`${prefix}. Версия настроек: ${data.settings_revision}; модель: ${data.model_version}`+(data.analysis_revision?`; расчёт: ${data.analysis_revision}.`:'.');}
export async function loadSettings(){
  status.textContent='Загружаем настройки…';reload.hidden=true;form.setAttribute('aria-busy','true');
  try{const result=await api('/model/settings');saved=result.data;etag=result.etag;populate(saved);savedStatus(saved);}
  catch(error){status.textContent=errorText(error);reload.hidden=false;}
  finally{form.setAttribute('aria-busy','false');}
}
form.addEventListener('input',()=>{settingsDirty=true;});
form.addEventListener('submit',async event=>{
  event.preventDefault();clearErrors();
  const weights={};let valid=true;
  for(const feature of saved.features){const input=document.getElementById('weight-'+feature.id);const value=Number(input.value);weights[feature.id]=value;if(input.value.trim()===''||!Number.isFinite(value)||value<feature.min_weight||value>feature.max_weight){fieldError(input.id,`Введите число от ${feature.min_weight} до ${feature.max_weight}.`);valid=false;}}
  if(!Object.values(weights).some(value=>value>0)){for(const feature of saved.features)fieldError('weight-'+feature.id,'Хотя бы один вес должен быть больше нуля.');valid=false;}
  const horizon=Number(document.querySelector('#horizon').value), medium=Number(document.querySelector('#medium').value),high=Number(document.querySelector('#high').value);
  if(!Number.isInteger(horizon)||horizon<1||horizon>90){fieldError('horizon','Введите целое число дней от 1 до 90.');valid=false;}
  if(!(medium>0&&medium<high&&high<=100)){fieldError('medium','Нужны пороги: 0 < средний < высокий ≤ 100.');fieldError('high','Высокий порог должен быть больше среднего и не выше 100.');valid=false;}
  if(!valid){form.querySelector('[aria-invalid="true"]')?.focus();return;}
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
  }finally{save.disabled=false;save.textContent='Сохранить и пересчитать';save.removeAttribute('aria-busy');document.querySelector('#cancel-settings').disabled=false;}
});
document.querySelector('#cancel-settings').addEventListener('click',()=>{if(saved){populate(saved);savedStatus(saved,'Изменения отменены');}});
reload.addEventListener('click',()=>{loadSettings();});
try{await startShell('settings');await loadSettings();}catch(error){status.textContent=errorText(error);}
