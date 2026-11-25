const controls = document.querySelector("#comparison-controls");
const cases = document.querySelector("#compare-case");
const left = document.querySelector("#compare-left");
const right = document.querySelector("#compare-right");
const grid = document.querySelector(".comparison-grid");
const panels = [...grid.querySelectorAll("[data-case]")];
const inspections = document.querySelector(".inspection-list");
const details = [...inspections.querySelectorAll("[data-pair]")];
const empty = document.querySelector("#comparison-empty");
const status = document.querySelector("#comparison-status");
const selectedCase = document.querySelector("#selected-case");
const views = [...document.querySelectorAll("[data-report-view]")];
const navigation = [...document.querySelectorAll('nav[aria-label="Report"] a')];
const overview = document.querySelector("#overview");
let currentView = overview;
let previousReviewers = [left.value, right.value];
let activeClaim = "";

function showComparison(announce = false, requestedClaim = "") {
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
  const caseOption = cases.selectedOptions[0];
  selectedCase.textContent = caseOption?.dataset.title || "";
  selectedCase.hidden = !selectedCase.textContent;
  const links = visible.flatMap(panel => [...panel.querySelectorAll(".claim-index a")]);
  const active = links.find(link => link.hash === (requestedClaim || activeClaim))
    || links.find(link => link.dataset.decision !== "out_of_scope") || links[0];
  activeClaim = active?.hash || "";
  links.forEach(link => {
    if (link === active) link.setAttribute("aria-current", "true");
    else link.removeAttribute("aria-current");
  });
  for (const detail of details) detail.hidden = `#${detail.id}` !== activeClaim;
  inspections.hidden = !active;
  if (announce) {
    const caseLabel = caseOption?.textContent;
    const names = visible.map(panel => panel.querySelector("h3").textContent);
    status.textContent = names.length
      ? `${caseLabel}. Showing ${names.join(" and ")}.`
      : `${caseLabel}. No reviews available for this selection.`;
  }
}

function showRoute(scroll = true) {
  const target = document.getElementById(location.hash.slice(1));
  currentView = target?.closest("[data-report-view]") || (target?.id === "main" ? currentView : overview);
  for (const view of views) view.hidden = view !== currentView;
  for (const link of navigation) {
    if (link.hash === `#${currentView.id}`) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  }

  const panel = target?.dataset.pair ? document.getElementById(target.dataset.pair) : target?.closest("[data-case]");
  if (panel) {
    cases.value = panel.dataset.case;
    if (![left.value, right.value].includes(panel.dataset.provider)) left.value = panel.dataset.provider;
    showComparison(false, target.dataset.pair ? `#${target.id}` : "");
  }
  if (scroll) {
    const destination = target || currentView;
    destination.focus({ preventScroll: true });
    destination.scrollIntoView({ block: "start" });
  }
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
}

document.addEventListener("click", event => {
  const link = event.target.closest('a[href^="#"]');
  if (link && link.hash === location.hash && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
    event.preventDefault();
    showRoute();
  }
});
window.addEventListener("hashchange", () => showRoute());
showComparison();
showRoute(Boolean(location.hash));
