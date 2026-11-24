const controls = document.querySelector("#comparison-controls");
const cases = document.querySelector("#compare-case");
const left = document.querySelector("#compare-left");
const right = document.querySelector("#compare-right");
const grid = document.querySelector(".comparison-grid");
const panels = [...grid.querySelectorAll("[data-case]")];
const empty = document.querySelector("#comparison-empty");

function showComparison() {
  const selected = [...new Set([left.value, right.value].filter(Boolean))];
  let visible = 0;
  for (const panel of panels) {
    panel.hidden = panel.dataset.case !== cases.value || !selected.includes(panel.dataset.provider);
    if (!panel.hidden) visible += 1;
  }
  for (const provider of selected) {
    for (const panel of panels.filter(item => !item.hidden && item.dataset.provider === provider)) {
      grid.append(panel);
    }
  }
  empty.hidden = visible > 0;
}

function showLinkedPanel() {
  const panel = document.getElementById(location.hash.slice(1))?.closest("[data-case]");
  if (!panel) return;
  cases.value = panel.dataset.case;
  if (![left.value, right.value].includes(panel.dataset.provider)) left.value = panel.dataset.provider;
  if (left.value === right.value) {
    right.value = [...right.options].find(option => option.value !== left.value)?.value || "";
  }
  showComparison();
  document.getElementById(location.hash.slice(1))?.scrollIntoView();
}

if (panels.length) {
  controls.hidden = false;
  controls.addEventListener("change", event => {
    if (left.value && left.value === right.value) {
      const other = event.target === right ? left : right;
      other.value = [...other.options].find(option => option.value !== event.target.value)?.value || "";
    }
    showComparison();
  });
  window.addEventListener("hashchange", showLinkedPanel);
  showComparison();
  showLinkedPanel();
}
