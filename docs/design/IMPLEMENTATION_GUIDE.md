# Реализация и инструменты

## Состояние — 2026-10-22

`design/` — изолированная визуальная основа на native HTML/CSS/ES modules. Она содержит главную сцену, статичные страницы входа и регистрации, нейтральную оболочку, базовые элементы, демонстрацию Prism Glass и независимую заготовку Dashboard в `design/dashboard/`. Предметная область неизвестна: продуктовые разделы, команды, данные и стек не выбраны. Dashboard использует существующие токены и локальный JSON; её единственный пункт навигации — временный, а интерфейс остаётся вне probe. Python stdlib probe, Docker/Compose/Caddy сохраняют инфраструктурную роль.

Перед работой изучены AGENTS, README, challenge/rules/evaluation, architecture/implementation-plan/decisions, tools/infrastructure/demo, probe/server, Docker/CI, scripts/check.py и все четыре локальных навыка. Предсуществующие изменения сохраняются; точная фиксация дизайна выполняется отдельно.

## Проверенные инструменты

| Инструмент | Фактическое применение |
|---|---|
| Python 3.13.7 / Node 24.13.0 | Offline checks и отдельные build/test scripts; не выбор продуктового стека |
| FontTools 4.67.0 + WOFF2 codecs | Установлены только в gitignored `.state/design-tools`; чтение cmap PP Neue Machina Inktrap и экспорт SVG-контуров wordmark; шрифтовой файл копируется без изменения |
| Sharp 0.35.5 | Bundled build tool: lossless WebP и мобильный resize, сравнение decoded pixels |
| Playwright 1.62.1 | Bundled Node tool, изолированные Edge/Firefox/WebKit, 26 cases; не зависимость runtime/CI |
| Browser cua_repl | Реальные взаимодействия и визуальный просмотр узкого/широкого макета |
| ImageGen | Реальная генерация включённых колонн; исходник/происхождение/prompt сохранены |
| Figma MCP | Identity/auth read успешен. Доступ/запись конкретного файла не проверялись; файла назначения нет |
| Pencil MCP | Приложение не подключено к transport; операции не выполнены |
| Pillow / NumPy | Найдены на машине; не применялись к финальному изображению и не нужны runtime |

Регистрация инструмента не равна успешному выполнению. Новые подключения, облачные сервисы и runtime-библиотеки для этой подготовки не нужны. Секреты и идентификаторы аккаунтов в отчёт не сохраняются.

Применены `impeccable` (init/brand/typeset/polish) и `imagegen`; проектная процедура demo-qa изучена с ограничением на отсутствие ТЗ. Figma prerequisite skills читать перед соответствующей будущей записью/импортом. Подготовка не даёт оснований придумывать MVP или развёртывать его до ТЗ.

## Минимальная основа

- `tokens.css` — один источник палитры, типографики, отступов, радиусов, слоёв, движения и параметров стекла.
- `fonts.css` — один локальный PP Neue Machina Inktrap variable; весь интерфейс использует его по последнему уточнению пользователя. Исходные байты и происхождение проверяются по manifest.
- `foundation.css` — scoped `.ds-root` и native controls; `preview.html/css/js` показывают связанные ошибки, select, focus и dialog.
- `index.html`, `scene.css/js` — корневая сцена `/` с фотофоном и Bayer Canvas; на ПК снимок целиком помещается в viewport, заголовок `localhost3000` и действие `CONNECT` остаются текстом. Ссылка использует выбранный Liquid Glass модуль с поддержкой и доступными fallback и ведёт на `login.html`. Внешней прокрутки нет; `#workspace` сохраняет переход по истории и фокус, no-JS показывает исходное фото и матовую кнопку.
- `login.html`, `register.html`, `auth-page.css/js` — статичные визуальные формы с тем же фоном и дизерингом, токенизированными полями, проверкой обязательности/формата почты/совпадения паролей и доступным показом пароля. Кнопки форм используют Liquid Glass. После корректной проверки отправка не выполняется; backend и правила сложности пароля не выбраны.
- `prism.js` — маленький WebGL 1 sampler собственной картинки. Статическая отрисовка без Three.js, захвата DOM или постоянного RAF; равноправный непрозрачный fallback.
- `dither.html`, `dither.js`, `dither-preview.js` — изолированное сравнение выбранного пользователем Aceternity Bayer/duotone на Canvas 2D. Без React/Tailwind/Motion и выбора продуктового стека; источник/границы в `design/tooling/aceternity-dither.json`. Главная сцена использует прежнюю графику.
- `liquid-glass.html`, `liquid-glass.js`, `liquid-glass-optics.js` — выбранное Aceternity Liquid Glass, исходные SVG/displacement helpers и native-lifecycle. Пресет Controls: circle / 50 / 20 / 60 / 4.8 / 0.05. `tooling/liquid-glass-controls.tsx` содержит сгенерированный `useDialKit`-адаптер для будущей React-интеграции; он не запускается текущим native-стендом.

Локальный запуск из корня:

```powershell
python -m http.server 3300 --bind 127.0.0.1 --directory design
# http://127.0.0.1:3300/
# http://127.0.0.1:3300/preview.html
# http://127.0.0.1:3300/dither.html
# http://127.0.0.1:3300/liquid-glass.html
# http://127.0.0.1:3300/login.html
# http://127.0.0.1:3300/register.html
```

Сервер публикует только `design` на loopback. Он не заменяет probe, Docker/Compose или `/healthz`. Остановка Ctrl+C; при занятом порте выбрать свободный. Сборка ассетов и browser tests: [design/tooling/README.md](../../design/tooling/README.md). Offline `python scripts/check.py` использует только stdlib и проверяет также дизайн-файлы.

## Перенос после ТЗ

Уточнение образца Liquid Glass: `liquid-glass.html` также показывает размещение по Hero Section Aceternity (плавающая панель, плашка и вторичное действие). Для этих поверхностей native-адаптер читает радиус из CSS; круглую тестовую линзу и defaults Controls это не меняет. Иконки — используемые SVG HackerNoon Pixel Icon Library из `assets/icons`, с атрибуцией на `icon-credits.html`, без icon font и runtime-пакета. Карточки остаются плоскими, hover/active кнопок мгновенные.

1. Сохранить ТЗ/регламент/критерии; проверить допустимость заранее подготовленных CSS/ассетов, AI и инструментов.
2. Выбрать простой стек под основной сценарий. Нынешняя подготовка не обязывает к Python, React или Tailwind.
3. Подключить токены один раз; адаптер framework ссылается на `var(--...)`, без второго источника значений. Перенести локальные fonts/licenses и provenance ассетов.
4. Заменить нейтральную оболочку реальным сценарием и осмысленными пунктами Terminal Bar. Создавать компоненты/эффекты по потребности.
5. Держать материал в одном модуле. Для собственной текстуры использовать текущий sampler; произвольный DOM требует отдельной реализации и проверки.
6. При замене probe согласованно обновить Docker, health, smoke, CI и запуск. Пройти реальный product E2E на узком/широком экране и [дизайн-чеклист](DESIGN_QA_CHECKLIST.md).

Прямые CSS/native Web Animations достаточны для коротких переходов. Motion/GSAP/Three.js добавлять лишь при конкретной задаче после оценки версии, лицензии, сопровождения и размера. Рецепты и первоисточники — в [EFFECTS.md](EFFECTS.md); фактические результаты и открытые проверки — в [VERIFICATION.md](VERIFICATION.md).
