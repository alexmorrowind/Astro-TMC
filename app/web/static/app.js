document.addEventListener("click", (event) => {
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
