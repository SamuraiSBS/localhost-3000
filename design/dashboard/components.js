const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");

const getIconClass = (item) => {
  if (item.icon === "home") return "ds-icon--arrow-right";
  if (item.icon === "overview") return item.current ? "ds-icon--info-solid" : "ds-icon--info";
  return "";
};

const renderIcon = (item) => {
  const iconClass = getIconClass(item);
  return iconClass ? `<span class="ds-icon ${iconClass}" aria-hidden="true"></span>` : "";
};

export function renderSidebar({ brand, homeHref, items, note }) {
  const navigation = items.map((item) => `
    <li>
      <a href="${escapeHtml(item.href)}"${item.current ? ' aria-current="page"' : ""}>
        ${renderIcon(item)}
        <span class="dashboard-nav__label">${escapeHtml(item.label)}</span>
      </a>
    </li>`).join("");

  return `
    <div class="dashboard-sidebar__top">
      <a class="dashboard-brand" href="${escapeHtml(homeHref)}" aria-label="${escapeHtml(brand)} — на главную">${escapeHtml(brand)}</a>
    </div>
    <nav aria-label="Основная навигация">
      <ul class="dashboard-nav">${navigation}</ul>
    </nav>
    <p class="dashboard-sidebar__note" data-sidebar-note>${escapeHtml(note)}</p>`;
}

export function renderMobileDock({ homeHref, items, id = "dashboard-mobile-dock" }) {
  const links = [
    { label: "Главная", href: homeHref, icon: "home" },
    ...items,
  ];
  const navigation = links.map((item) => `
    <li>
      <a class="dashboard-mobile-dock__item" href="${escapeHtml(item.href)}" aria-label="${escapeHtml(item.label)}" title="${escapeHtml(item.label)}"${item.current ? ' aria-current="page"' : ""}>
        ${renderIcon(item)}
        <span class="dashboard-mobile-dock__label" aria-hidden="true">${escapeHtml(item.label)}</span>
      </a>
    </li>`).join("");

  return `
    <button class="ds-button ds-button--secondary dashboard-mobile-dock__toggle" type="button" data-dock-toggle aria-controls="${escapeHtml(id)}-items" aria-expanded="false" aria-label="Открыть навигацию">
      <span class="dashboard-mobile-dock__toggle-icon" aria-hidden="true"><span></span><span></span><span></span></span>
    </button>
    <ul class="dashboard-mobile-dock__menu" id="${escapeHtml(id)}-items" data-dock-menu aria-hidden="true" inert>
      ${navigation}
    </ul>`;
}

export function renderHeader({ title, description, demoLabel }) {
  return `
    <div>
      <h1>${escapeHtml(title)}</h1>
      <p>${escapeHtml(description)}</p>
    </div>
    <p class="dashboard-header__demo">${escapeHtml(demoLabel)}</p>`;
}

export function renderStatCard({ label, value }) {
  return `
    <li class="dashboard-stat">
      <p class="dashboard-stat__label">${escapeHtml(label)}</p>
      <p class="dashboard-stat__value">${escapeHtml(value)}</p>
    </li>`;
}

export function renderRecord(record) {
  return `
    <li class="dashboard-record">
      <h3>${escapeHtml(record.title)}</h3>
      <p>${escapeHtml(record.description)}</p>
    </li>`;
}

export function renderStats(stats) {
  if (!stats.length) {
    return '<p class="dashboard-empty">Показатели появятся вместе с данными будущего проекта.</p>';
  }
  return `<ul class="dashboard-stats">${stats.map(renderStatCard).join("")}</ul>`;
}

export function renderRecords(records) {
  if (!records.length) {
    return '<p class="dashboard-empty">В наборе пока нет демонстрационных записей.</p>';
  }
  return `<ul class="dashboard-records">${records.map(renderRecord).join("")}</ul>`;
}

export function renderRecordsPanel(records) {
  return `
    <section class="dashboard-panel dashboard-panel--records" aria-labelledby="records-title">
      <div class="dashboard-panel__heading">
        <h2 id="records-title">Пример списка</h2>
        <p>Демо-записи показывают структуру будущего блока.</p>
      </div>
      ${renderRecords(records)}
    </section>`;
}

export function renderFuturePanel() {
  return `
    <section class="dashboard-panel dashboard-panel--future" aria-labelledby="future-title">
      <h2 id="future-title">Место для модуля</h2>
      <p>Содержимое и действия определятся после получения ТЗ.</p>
    </section>`;
}

export function renderDashboard(data) {
  return `
    <section class="dashboard-section" aria-labelledby="stats-title">
      <div class="dashboard-section__heading">
        <h2 id="stats-title">Сводные показатели</h2>
        <p>Тестовые значения для замены на данные будущего проекта.</p>
      </div>
      ${renderStats(data.stats)}
    </section>
    <div class="dashboard-panels">
      ${renderRecordsPanel(data.records)}
      ${renderFuturePanel()}
    </div>`;
}

export function renderLoading() {
  const skeletonPanel = (lines) => `
    <div class="dashboard-skeleton--panel">
      <div class="dashboard-skeleton dashboard-skeleton--label"></div>
      ${Array.from({ length: lines }, () => '<div class="dashboard-skeleton dashboard-skeleton--line"></div>').join("")}
    </div>`;

  return `
    <p class="dashboard-feedback" role="status">Загружаем демо-данные…</p>
    <div class="dashboard-loading-stats" aria-hidden="true">
      ${Array.from({ length: 4 }, () => `
        <div class="dashboard-skeleton-stat">
          <div class="dashboard-skeleton dashboard-skeleton--label"></div>
          <div class="dashboard-skeleton dashboard-skeleton--value"></div>
        </div>`).join("")}
    </div>
    <div class="dashboard-loading-panels" aria-hidden="true">
      ${skeletonPanel(3)}
      ${skeletonPanel(2)}
    </div>`;
}

export function renderError() {
  return `
    <section class="dashboard-message" role="alert" aria-labelledby="load-error-title">
      <h2 id="load-error-title">Не удалось загрузить демо-данные</h2>
      <p>Проверьте подключение к локальному серверу и повторите попытку.</p>
      <button class="ds-button ds-button--secondary" type="button" data-dashboard-retry>Повторить</button>
    </section>`;
}
