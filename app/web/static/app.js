document.addEventListener("click", (event) => {
  const sidebarToggle = event.target.closest("[data-sidebar-toggle]");
  if (sidebarToggle) {
    const sidebar = document.querySelector("[data-sidebar]");
    if (sidebar) {
      const isCollapsed = sidebar.classList.toggle("is-collapsed");
      localStorage.setItem("tmcSidebarCollapsed", isCollapsed ? "1" : "0");
      sidebarToggle.setAttribute("aria-label", isCollapsed ? "Развернуть меню" : "Свернуть меню");
      sidebarToggle.setAttribute("title", isCollapsed ? "Развернуть меню" : "Свернуть меню");
    }
    return;
  }

  const languageButton = event.target.closest(".language-button");
  if (languageButton) {
    const target = document.getElementById(languageButton.dataset.templateTarget);
    if (target) {
      target.value = languageButton.dataset.templateText || "";
    }
    const menu = languageButton.closest(".language-menu");
    if (menu) {
      menu.querySelectorAll(".language-button").forEach((button) => {
        button.classList.toggle("active", button === languageButton);
      });
    }
    return;
  }

  const toggle = event.target.closest(".details-toggle");
  if (!toggle) {
    return;
  }

  const targetId = toggle.dataset.target;
  const target = document.getElementById(targetId);
  if (!target) {
    return;
  }

  target.hidden = !target.hidden;
  toggle.textContent = target.hidden ? "More details" : "Hide details";
});

document.addEventListener("input", (event) => {
  const search = event.target.closest("[data-chat-search]");
  if (!search) {
    return;
  }

  const needle = search.value.trim().toLowerCase();
  const picker = document.querySelector("[data-chat-picker]");
  if (!picker) {
    return;
  }

  let visibleCount = 0;
  picker.querySelectorAll("[data-chat-search-text]").forEach((card) => {
    const haystack = (card.dataset.chatSearchText || "").toLowerCase();
    const isVisible = !needle || haystack.includes(needle);
    card.hidden = !isVisible;
    if (isVisible) {
      visibleCount += 1;
    }
  });

  const emptyState = picker.querySelector("[data-chat-search-empty]");
  if (emptyState) {
    emptyState.hidden = visibleCount !== 0;
  }
});

document.addEventListener("DOMContentLoaded", () => {
  const sidebar = document.querySelector("[data-sidebar]");
  const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
  if (!sidebar) {
    return;
  }

  const isCollapsed = localStorage.getItem("tmcSidebarCollapsed") === "1";
  sidebar.classList.toggle("is-collapsed", isCollapsed);
  if (sidebarToggle) {
    sidebarToggle.setAttribute("aria-label", isCollapsed ? "Развернуть меню" : "Свернуть меню");
    sidebarToggle.setAttribute("title", isCollapsed ? "Развернуть меню" : "Свернуть меню");
  }
});
