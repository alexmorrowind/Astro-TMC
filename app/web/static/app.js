document.addEventListener("click", (event) => {
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
