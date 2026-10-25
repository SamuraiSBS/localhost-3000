import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AnimatePresence, MotionConfig, motion, useReducedMotion } from 'motion/react';
import { ArrowRight, ArrowUpRight, Check, ChevronDown, Eye, EyeOff, Info, Menu, Plus, RotateCcw, Settings2, SquareArrowOutUpRight, X } from 'lucide-react';
import FaultyTerminal from './vendor/faulty/FaultyTerminal';
import BlurText from './vendor/blur/BlurText';
import { LiquidGlass } from './vendor/glass/liquid-glass';
import { InteractiveHoverButton } from './vendor/button/interactive-hover-button';
import { FileUpload } from './vendor/upload/file-upload';
import './theme.css';
const Chart21st = React.lazy(() => import('./vendor/chart/Chart21st'));
const AnimatedContent = React.lazy(() => import('./vendor/animated/AnimatedContent'));

const SOURCES = {
  faulty: 'https://reactbits.dev/backgrounds/faulty-terminal',
  button: 'https://magicui.design/docs/components/interactive-hover-button',
  blur: 'https://reactbits.dev/text-animations/blur-text',
  animated: 'https://reactbits.dev/animations/animated-content',
  glass: 'https://ui.aceternity.com/components/liquid-glass',
  chart: 'https://21st.dev/@sean0205/components/c-chart-11',
  upload: 'https://ui.aceternity.com/components/file-upload',
};

function Source({ name, label = 'Посмотреть оригинал' }: {name: keyof typeof SOURCES; label?: string}) {
  return <a className="source-link" href={SOURCES[name]} target="_blank" rel="noreferrer">{label}<ArrowUpRight aria-hidden /></a>;
}
function Control({ children, variant = '', className = '', busy, ...props }: any) {
  return <button type="button" className={`control ${variant} ${className}`} aria-busy={busy || undefined} {...props}>{busy && <span className="busy-mark" aria-hidden />}{children}</button>;
}
function Chapter({ id, title, description, children }: any) {
  return <section id={id} className="chapter shell" aria-labelledby={`${id}-heading`}><div className="chapter-head"><h2 id={`${id}-heading`}>{title}</h2><p>{description}</p></div>{children}</section>;
}
function Sample({ title, description, children, className = '' }: any) {
  return <div className={`sample ${className}`}><h3>{title}</h3>{description && <p>{description}</p>}{children}</div>;
}

function App() {
  const reduced = useReducedMotion();
  const [paused, setPaused] = useState(false);
  const [mono, setMono] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const [radius, setRadius] = useState('8px');
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState('');
  const [saved, setSaved] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [formError, setFormError] = useState(false);
  const [autoUpdate, setAutoUpdate] = useState(true);
  const [weight, setWeight] = useState(65);
  const [reveal, setReveal] = useState(0);
  const [activeTab, setActiveTab] = useState('Все');
  const [query, setQuery] = useState('');
  const [descending, setDescending] = useState(true);
  const [chosenFile, setChosenFile] = useState<File | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const busyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [docHidden, setDocHidden] = useState(document.hidden);

  useEffect(() => {
    const visibility = () => setDocHidden(document.hidden);
    document.addEventListener('visibilitychange', visibility);
    return () => { document.removeEventListener('visibilitychange', visibility); clearTimeout(toastTimer.current); clearTimeout(busyTimer.current); };
  }, []);
  useEffect(() => {
    if (!navOpen) return;
    const close = (e: KeyboardEvent) => { if (e.key === 'Escape') { setNavOpen(false); menuButton.current?.focus(); } };
    const outside = (e: PointerEvent) => { if (!(e.target as Element).closest('.topbar')) setNavOpen(false); };
    document.addEventListener('keydown', close);
    document.addEventListener('pointerdown', outside);
    return () => { document.removeEventListener('keydown', close); document.removeEventListener('pointerdown', outside); };
  }, [navOpen]);

  function notify(message: string) {
    clearTimeout(toastTimer.current);
    setToast(message);
    toastTimer.current = setTimeout(() => setToast(''), 6000);
  }
  function simulateSave() {
    if (busy) return;
    setBusy(true); setSaved(false);
    busyTimer.current = setTimeout(() => { setBusy(false); setSaved(true); notify('Демонстрация завершена. Настройки сайта не изменялись.'); }, 1600);
  }
  const data = [
    { id: '1042', email: 'client-1042@example.test', risk: 83, level: 'Высокий', money: '420,00 €' },
    { id: '1068', email: 'client-1068@example.test', risk: 67, level: 'Высокий', money: '186,00 €' },
    { id: '1081', email: 'client-1081@example.test', risk: 38, level: 'Средний', money: '74,00 €' },
    { id: '1105', email: 'client-1105@example.test', risk: 12, level: 'Низкий', money: '12,00 €' },
  ];
  const filtered = data.filter(x => (activeTab !== 'Высокий риск' || x.level === 'Высокий') && `${x.id} ${x.email}`.includes(query.trim().toLowerCase())).sort((a,b) => descending ? b.risk - a.risk : a.risk - b.risk);
  const openDetails = (id: string) => { notify(`Это образец строки клиента ${id}. Рабочая карточка не открывается из демонстрационных данных.`); };

  return <MotionConfig reducedMotion="user">
    <a className="skip" href="#controls">К элементам интерфейса</a>
    <header className="topbar"><div className="shell">
      <a className="brand" href="#top"><Plus aria-hidden />localhost3000</a>
      <nav className="topnav" data-open={navOpen} id="lab-navigation" aria-label="Разделы образца">
        {[['controls','Кнопки'],['forms','Поля'],['data','Данные'],['motion','Движение']].map(([id,label]) => <a key={id} href={`#${id}`} onClick={() => setNavOpen(false)}>{label}</a>)}
      </nav>
      <button ref={menuButton} className="control icon mobile-menu" type="button" aria-label={navOpen ? 'Закрыть меню' : 'Открыть меню'} aria-controls="lab-navigation" aria-expanded={navOpen} onClick={() => setNavOpen(!navOpen)}>{navOpen ? <X aria-hidden /> : <Menu aria-hidden />}</button>
    </div></header>

    <main id="top">
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero-bg" aria-hidden>
          <FaultyTerminal key={mono ? 'mono' : 'phosphor'} tint={mono ? '#E4E4E7' : '#a7ef9e'} glyphs={mono ? 'characters' : 'blocks'} scale={mono ? 2.4 : 1.5} digitSize={mono ? 1.1 : 1.2} timeScale={.5} brightness={mono ? .7 : .75} glow={mono ? 1 : .5} persistence={mono ? .37 : .5} mouseStrength={.5} mouseGlow={.6} scanlineIntensity={mono ? .3 : .5} curvature={mono ? 0 : .1} clickEffect={mono ? 'none' : 'glitch'} mouseReact={!mono} scramble={mono ? .9 : .5} glitchAmount={mono ? 1.1 : 1} vignette={.25} variation={.42} pause={paused || docHidden || !!reduced} pageLoadAnimation={!reduced} dpr={1.5} />
        </div>
        <div className="hero-content shell">
          <div className="hero-copy">
            <h1 id="hero-title">Детали, которые оживляют систему.</h1>
            <p>Форма, свет и движение работают вместе. Здесь можно потрогать будущий интерфейс до его переноса на рабочие страницы.</p>
            <InteractiveHoverButton className="original-button" aria-label="Исследовать элементы" onClick={() => document.getElementById('controls')?.scrollIntoView({ behavior: reduced ? 'instant' : 'smooth' })}>Исследовать элементы</InteractiveHoverButton>
            <span className="hero-note">Интерактивный дизайн-образец. Данные демонстрационные.</span>
          </div>
          <LiquidGlass radius={12} bevelDepth={14} scale={48} aberration={4} frost={.4} tint={.12} className="glass-preview">
            <Settings2 size={24} aria-hidden />
            <p>Настоящее преломление.<br />Читаемый текст поверх.</p>
            <a href={SOURCES.glass} target="_blank" rel="noreferrer">Оригинал Liquid Glass <ArrowUpRight aria-hidden /></a>
          </LiquidGlass>
        </div>
        <div className="hero-controls"><div className="shell">
          <div className="segmented" aria-label="Вариант фона">
            <button aria-pressed={!mono} onClick={() => setMono(false)}>Phosphor из оригинала</button>
            <button aria-pressed={mono} onClick={() => setMono(true)}>Монохром из проекта</button>
          </div>
          <Control onClick={() => setPaused(!paused)} aria-pressed={paused}>{paused ? 'Продолжить фон' : 'Остановить фон'}</Control>
          <Source name="faulty" />
        </div></div>
      </section>

      <Chapter id="controls" title="Кнопка — это поведение." description="Проверь наведение, нажатие и Tab. Оригинальная кнопка Magic UI показана отдельно; рядом — варианты для рабочих экранов.">
        <div className="sample-grid">
          <Sample title="Оригинал Interactive Hover Button" description="Расширяющаяся точка, уходящий текст и появляющаяся стрелка. Исходник компонента сохранён без изменений.">
            <InteractiveHoverButton className="original-button" aria-label="Открыть систему" onClick={() => notify('Нажатие работает. Это демонстрация оригинальной кнопки Magic UI.')}>Открыть систему</InteractiveHoverButton>
            <div><Source name="button" /></div>
          </Sample>
          <Sample title="Геометрия рабочих элементов" description="Предлагаю радиус 8 px: собранная форма, удобные поля, одинаковая высота 48 px.">
            <div className="segmented" aria-label="Форма рабочих кнопок">{[['2px','Строгая'],['8px','Скруглённая'],['999px','Капсула']].map(([value,label]) => <button key={value} aria-pressed={radius === value} onClick={() => setRadius(value)}>{label}</button>)}</div>
            <div className="row" style={{ '--control-shape': radius } as React.CSSProperties}>
              <Control variant="primary" onClick={() => notify('Главное действие: движущаяся заливка и стрелка.')}>Применить<ArrowRight aria-hidden /></Control>
              <Control onClick={() => notify('Вторичное действие: спокойная подсветка поверхности.')}>Отменить</Control>
            </div>
          </Sample>
          <Sample title="Полный набор состояний" description="Кнопка сохраняет форму во время действия. В образце задержка демонстрационная, на сайте она будет зависеть от реального запроса.">
            <div className="row"><Control variant="primary" className="state-action" busy={busy} disabled={busy} onClick={simulateSave}>{busy ? 'Сохраняем…' : saved ? <><Check aria-hidden />Готово</> : 'Проверить загрузку'}</Control><Control disabled>Недоступно</Control></div>
            <div className="row"><Control variant="ghost" onClick={() => notify('Текстовое действие выполнено в образце.')}>Подробнее<ArrowUpRight aria-hidden /></Control><Control variant="danger" onClick={() => dialog.current?.showModal()}>Опасное действие</Control></div>
          </Sample>
          <Sample title="Иконки, подсказки, фокус" description="Подсказка появляется мышью и клавиатурой. Escape закрывает её. Иконка получает собственное доступное имя.">
            <div className="row">
              <span className="tooltip-wrap" data-dismissed={dismissed} onPointerEnter={() => setDismissed(false)} onFocus={() => setDismissed(false)} onKeyDown={e => { if(e.key === 'Escape') setDismissed(true); }}>
                <Control className="icon" aria-label="О расчёте" aria-describedby="calculation-tip" onClick={() => setDismissed(false)}><Info aria-hidden /></Control>
                <span className="tooltip" role="tooltip" id="calculation-tip">Индекс отражает риск непродления. Это не вероятность ухода.</span>
              </span>
              <Control className="icon" aria-label="Повторить демонстрацию" onClick={() => notify('Демонстрация повторена.')}><RotateCcw aria-hidden /></Control>
              <Control onClick={() => dialog.current?.showModal()}>Открыть диалог<ArrowUpRight aria-hidden /></Control>
            </div>
          </Sample>
        </div>
      </Chapter>

      <Chapter id="forms" title="Поля с характером." description="Подпись остаётся видимой. Фокус, ошибка, показ пароля и выбранные состояния различимы без опоры только на цвет.">
        <div className="sample-grid">
          <Sample title="Ввод и проверка" description="Форма работает локально. Пароль и другие значения не отправляются на сервер.">
            <form noValidate onSubmit={e => { e.preventDefault(); const form = new FormData(e.currentTarget); const invalid = !String(form.get('login') || '').trim(); setFormError(invalid); if(invalid) (e.currentTarget.elements.namedItem('lab-login') as HTMLInputElement)?.focus(); else notify('Форма проверена локально. Вход в приложение не выполнялся.'); }}>
              <div className="field"><label htmlFor="lab-login">Логин</label><input className="input" id="lab-login" name="login" autoComplete="off" placeholder="Введите логин" aria-invalid={formError} aria-describedby={formError ? 'lab-login-error' : 'lab-login-hint'} onChange={() => setFormError(false)} />{formError ? <p className="error" id="lab-login-error"><Info aria-hidden size={20} />Введите логин, чтобы продолжить.</p> : <p className="hint" id="lab-login-hint">Пустая форма покажет состояние ошибки.</p>}</div>
              <div className="field"><label htmlFor="lab-password">Пароль</label><div className="password-wrap"><input className="input" id="lab-password" type={showPassword ? 'text' : 'password'} placeholder="Демонстрационное поле" autoComplete="off" /><button type="button" aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'} aria-pressed={showPassword} onClick={() => setShowPassword(!showPassword)}>{showPassword ? <EyeOff aria-hidden /> : <Eye aria-hidden />}</button></div></div>
              <Control type="submit" variant="primary">Проверить форму<ArrowRight aria-hidden /></Control>
            </form>
          </Sample>
          <Sample title="Выбор и настройка" description="Чекбокс, переключатель и ползунок имеют разные роли. Для диапазона всегда видно точное значение.">
            <div className="field"><label htmlFor="lab-period">Горизонт расчёта</label><select className="select" id="lab-period" defaultValue="30"><option value="7">7 дней</option><option value="30">30 дней</option><option value="60">60 дней</option></select></div>
            <label className="check"><input type="checkbox" defaultChecked />Показывать причины риска</label>
            <button type="button" className="switch" role="switch" aria-checked={autoUpdate} onClick={() => setAutoUpdate(!autoUpdate)}><span>Автоматическое обновление</span><span className="switch-track" aria-hidden><span /></span></button>
            <div className="field"><label htmlFor="lab-weight">Вес признака: {weight}%</label><input className="range" type="range" id="lab-weight" min="0" max="100" value={weight} onChange={e => setWeight(Number(e.target.value))} /></div>
          </Sample>
        </div>
      </Chapter>

      <Chapter id="data" title="Данные отвечают на действие." description="В таблице — подсветка строки, поиск и сортировка. В оригинальном графике — раскрытие столбца. Все числа здесь демонстрационные.">
        <div className="tabs" role="tablist" aria-label="Клиенты в образце">{['Все','Высокий риск'].map((tab,i) => <button key={tab} type="button" role="tab" id={`tab-${i}`} aria-controls="demo-clients" aria-selected={activeTab===tab} tabIndex={activeTab===tab ? 0 : -1} onClick={() => setActiveTab(tab)} onKeyDown={e => { if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) { e.preventDefault(); const next = e.key==='Home' ? 0 : e.key==='End' ? 1 : 1-i; setActiveTab(['Все','Высокий риск'][next]); document.getElementById(`tab-${next}`)?.focus(); } }} >{tab}{activeTab===tab && <motion.span layoutId="tab-underline" className="tab-underline" />}</button>)}</div>
        <div role="tabpanel" id="demo-clients" aria-labelledby={activeTab === 'Все' ? 'tab-0' : 'tab-1'}>
          <div className="table-toolbar"><label className="sr-only" htmlFor="demo-search">Поиск демонстрационного клиента</label><input className="input" id="demo-search" type="search" placeholder="Найти ID или email" value={query} onChange={e => setQuery(e.target.value)} /><Control onClick={() => setDescending(!descending)}>Риск {descending ? 'по убыванию ↓' : 'по возрастанию ↑'}</Control><p role="status">Найдено: {filtered.length}</p></div>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Образец таблицы с горизонтальной прокруткой"><table className="demo-table"><caption className="sr-only">Демонстрационные клиенты, не данные приложения</caption><thead><tr><th scope="col">Клиент</th><th scope="col" aria-sort={descending ? 'descending' : 'ascending'}>Индекс риска</th><th scope="col">Уровень</th><th scope="col">Под риском</th></tr></thead><tbody>{filtered.map(row => <motion.tr key={row.id} layout={!reduced}><td><a href="#data" onClick={e => {e.preventDefault(); openDetails(row.id);}}>{row.email}</a></td><td>{row.risk} / 100</td><td><span className="risk">{row.level}</span></td><td>{row.money}</td></motion.tr>)}</tbody></table>{!filtered.length && <p className="hint py-6">Ничего не найдено. Измените запрос или выберите «Все».</p>}</div>
        </div>
        <div className="sample-grid mt-12">
          <div><h3>Оригинальный Animated Bar Chart</h3><p className="hint mb-6">Сохранены данные, цвета и движение демо ReUI. Наведи на столбец, чтобы раскрыть значение.</p><div className="chart-stage"><React.Suspense fallback={<p role="status">Загружаем образец графика…</p>}><Chart21st /></React.Suspense></div><Source name="chart" /><details className="source-details"><summary>Границы этого примера</summary><p>Это исходный график демо с месяцами и демонстрационными числами. На рабочем экране будут реальные интервалы продления и данные API. Клавиатурный и сенсорный выбор столбцов потребует отдельного адаптера; исходник сам обрабатывает наведение мыши.</p></details></div>
          <div><h3>Раскрытие подробностей</h3><p className="hint mb-6">Содержание открывается по запросу. Страница остаётся спокойной, объяснение находится рядом с действием.</p>{[['Почему клиент в группе риска?','На рабочем экране здесь будет вклад реальных признаков из текущего расчёта. В образце данные не загружаются.'],['Что означает стоимость под риском?','Это сценарная денежная оценка ближайших продлений. Она не означает уже потерянную выручку.'],['Что меняется после сохранения?','На рабочем сайте настройки сохраняются через защищённый API и запускают пересчёт. Этот образец настройки не меняет.']].map(([title,text]) => <details className="details" key={title}><summary>{title}<Plus size={20} aria-hidden /></summary><p>{text}</p></details>)}</div>
        </div>
      </Chapter>

      <Chapter id="motion" title="У каждого движения своя задача." description="Заголовок выходит из расфокуса. Связанные элементы появляются последовательно. Системная настройка уменьшения движения учитывается.">
        <div className="sample-grid">
          <Sample title="Blur Text — исходный эффект"><div className="reveal-stage" key={`blur-${reveal}`}><BlurText text="Из шума появляется смысл." delay={100} direction="top" trigger="load" /></div><Control onClick={() => setReveal(reveal+1)}><RotateCcw aria-hidden />Повторить появление</Control><div><Source name="blur" /></div></Sample>
          <Sample title="Animated Content — исходный эффект"><div key={`content-${reveal}`}><React.Suspense fallback={<p>Кому нужно внимание · Почему возник риск · Какая сумма под риском</p>}><AnimatedContent distance={32} duration={.7} blur={4} stagger={.12} trigger="load" style={{visibility:'visible'}}>{['Кому нужно внимание','Почему возник риск','Какая сумма под риском'].map(text => <div className="reveal-item" key={text}>{text}</div>)}</AnimatedContent></React.Suspense></div><div><Source name="animated" /></div></Sample>
        </div>
      </Chapter>

      <Chapter id="upload" title="Импорт, который можно понять." description="Оригинальная анимация Aceternity. Файл только выбирается локально; содержимое не читается, загрузка и замена данных не выполняются.">
        <div className="source-upload"><FileUpload onChange={files => setChosenFile(files[0] || null)} /><div className="upload-alternative"><label htmlFor="lab-file">Доступный выбор файла с клавиатуры</label><input id="lab-file" type="file" accept=".json,application/json" onChange={e => setChosenFile(e.target.files?.[0] || null)} /><p role="status" className="hint mt-4">{chosenFile ? `Выбран: ${chosenFile.name}. Файл остаётся на вашем устройстве.` : 'Файл не выбран.'}</p></div></div>
        <Source name="upload" /><details className="source-details"><summary>Что потребуется для рабочего импорта</summary><p>В оригинале есть кликабельный блок без отдельного доступного имени, поэтому здесь добавлен нативный выбор файла. В продукте сохранятся проверка JSON, подтверждение полной замены, ограничения размера и отдельная кнопка отправки.</p></details>
      </Chapter>
    </main>
    <footer className="footer shell"><p>Дизайн-образец для согласования. Рабочий сайт пока не изменён.</p><a href="../preview.html">Прежняя дизайн-система<ArrowUpRight className="inline ml-2" aria-hidden /></a></footer>

    <dialog ref={dialog} className="modal" aria-labelledby="modal-title" onClick={e => { if (e.target===dialog.current) { const r=dialog.current.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom) dialog.current.close(); } }}>
      <h2 id="modal-title">Подтверждение действия</h2><p>Так будет выглядеть диалог перед важным изменением. Сейчас никакие данные не удаляются и не заменяются.</p><div className="row"><Control variant="primary" onClick={() => {dialog.current?.close(); notify('Подтверждение показано. Данные приложения не изменялись.');}}>Подтвердить<Check aria-hidden /></Control><Control onClick={() => dialog.current?.close()}>Отмена</Control></div>
    </dialog>
    <div aria-live="polite" aria-atomic="true"><AnimatePresence>{toast && <motion.div className="toast" key="toast" initial={reduced ? false : {opacity:0,y:16}} animate={{opacity:1,y:0}} exit={{opacity:0,y:8}}><Check aria-hidden /><p>{toast}</p><button aria-label="Закрыть уведомление" onClick={() => setToast('')}><X aria-hidden /></button></motion.div>}</AnimatePresence></div>
  </MotionConfig>;
}

createRoot(document.getElementById('root')!).render(<App />);
