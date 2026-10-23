import {
  renderDashboard,
  renderError,
  renderHeader,
  renderLoading,
  renderMobileDock,
  renderSidebar,
} from "./components.js";

document.documentElement.classList.add("has-js");

const content = document.querySelector("#dashboard-content");
const announcer = document.querySelector("#dashboard-announcer");
const sidebar = document.querySelector("#dashboard-sidebar");
const mobileDock = document.querySelector("#dashboard-mobile-dock");
const header = document.querySelector("#dashboard-header");

const navigationItems = [{ label: "Обзор", href: "./", current: true, icon: "overview" }];

sidebar.innerHTML = renderSidebar({
  brand: "localhost3000",
  homeHref: "../index.html",
  items: navigationItems,
  note: "Пункты навигации появятся после получения ТЗ.",
});
const mobileLayout = window.matchMedia("(max-width: 48rem)");
mobileDock.innerHTML = renderMobileDock({
  homeHref: "../index.html",
  items: navigationItems,
});

const dockToggle = mobileDock.querySelector("[data-dock-toggle]");
const dockMenu = mobileDock.querySelector("[data-dock-menu]");

function setDockOpen(open) {
  const isOpen = open && mobileLayout.matches;
  mobileDock.classList.toggle("is-open", isOpen);
  dockMenu.inert = !isOpen;
  dockMenu.setAttribute("aria-hidden", String(!isOpen));
  dockToggle.setAttribute("aria-expanded", String(isOpen));
  dockToggle.setAttribute("aria-label", isOpen ? "Закрыть навигацию" : "Открыть навигацию");
}

mobileDock.addEventListener("click", (event) => {
  if (event.target.closest("[data-dock-toggle]")) {
    setDockOpen(dockToggle.getAttribute("aria-expanded") !== "true");
    return;
  }
  if (event.target.closest(".dashboard-mobile-dock__item")) setDockOpen(false);
});

mobileDock.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && dockToggle.getAttribute("aria-expanded") === "true") {
    setDockOpen(false);
    dockToggle.focus({ preventScroll: true });
  }
});

document.addEventListener("click", (event) => {
  if (!mobileDock.contains(event.target)) setDockOpen(false);
});

mobileLayout.addEventListener("change", () => setDockOpen(false));
setDockOpen(false);

header.innerHTML = renderHeader({
  title: "Обзор",
  description: "Универсальная основа для будущего проекта.",
  demoLabel: "Демонстрационные данные",
});

async function loadDashboard(restoreFocus = false) {
  content.setAttribute("aria-busy", "true");
  content.innerHTML = renderLoading();
  announcer.textContent = "";

  try {
    const response = await fetch("./demo-data.json", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("Demo data request failed");

    const data = await response.json();
    if (!data || !Array.isArray(data.stats) || !Array.isArray(data.records)) {
      throw new Error("Demo data has an unsupported shape");
    }

    content.innerHTML = renderDashboard(data);
    content.setAttribute("aria-busy", "false");
    announcer.textContent = "Демо-данные загружены.";
    if (restoreFocus) content.focus({ preventScroll: true });
  } catch {
    content.innerHTML = renderError();
    content.setAttribute("aria-busy", "false");
    announcer.textContent = "Не удалось загрузить демо-данные.";
    if (restoreFocus) content.querySelector("[data-dashboard-retry]")?.focus();
  }
}

content.addEventListener("click", (event) => {
  if (event.target.closest("[data-dashboard-retry]")) loadDashboard(true);
});

loadDashboard();
