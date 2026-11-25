const controls = document.querySelector("#comparison-controls");
const cases = document.querySelector("#compare-case");
const left = document.querySelector("#compare-left");
const right = document.querySelector("#compare-right");
const grid = document.querySelector(".comparison-grid");
const panels = [...grid.querySelectorAll("[data-case]")];
const empty = document.querySelector("#comparison-empty");
const status = document.querySelector("#comparison-status");
let previousReviewers = [left.value, right.value];

function showComparison(announce = false) {
  previousReviewers = [left.value, right.value];
  const visible = previousReviewers.filter(Boolean).map(provider =>
    panels.find(panel => panel.dataset.case === cases.value && panel.dataset.provider === provider)
  ).filter(Boolean);
  for (const panel of panels) {
    panel.hidden = !visible.includes(panel);
  }
  for (const panel of visible) grid.append(panel);
  grid.dataset.visible = visible.length;
  empty.hidden = visible.length > 0;
  if (announce) {
    const caseLabel = cases.selectedOptions[0]?.textContent;
    const names = visible.map(panel => panel.querySelector("h3").textContent);
    status.textContent = names.length
      ? `${caseLabel}. Showing ${names.join(" and ")}.`
      : `${caseLabel}. No reviews available for this selection.`;
  }
}

function showLinkedPanel() {
  const target = document.getElementById(location.hash.slice(1));
  const panel = target?.closest("[data-case]");
  if (!panel) return;
  cases.value = panel.dataset.case;
  if (![left.value, right.value].includes(panel.dataset.provider)) left.value = panel.dataset.provider;
  showComparison();
  target.focus({ preventScroll: true });
  target.scrollIntoView({ block: "start" });
}

if (panels.length) {
  controls.hidden = false;
  controls.addEventListener("change", event => {
    if (left.value && left.value === right.value) {
      if (event.target === right) left.value = previousReviewers[1];
      else right.value = previousReviewers[0];
    }
    showComparison(true);
  });
  document.querySelector("#coverage").addEventListener("click", event => {
    const link = event.target.closest("a.state");
    if (link && link.hash === location.hash && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
      event.preventDefault();
      showLinkedPanel();
    }
  });
  window.addEventListener("hashchange", showLinkedPanel);
  showComparison();
  showLinkedPanel();
}
