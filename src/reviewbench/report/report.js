const controls = document.querySelector("#comparison-controls");
const cases = document.querySelector("#compare-case");
const reviewerInputs = [...controls.querySelectorAll('input[name="reviewer"]')];
const reviewerCount = document.querySelector("#reviewer-count");
const reviewerActions = [...controls.querySelectorAll("[data-select-reviewers]")];
const grid = document.querySelector(".comparison-grid");
const panels = [...grid.querySelectorAll("[data-case]")];
const contexts = [...document.querySelectorAll("[data-context-case]")];
const issueColumns = [...document.querySelectorAll("[data-issue-provider]")];
const issueScrollHints = [...document.querySelectorAll(".issue-scroll-hint")];
const inspections = document.querySelector(".inspection-list");
const details = [...inspections.querySelectorAll("[data-pair]")];
const empty = document.querySelector("#comparison-empty");
const status = document.querySelector("#comparison-status");
const selectedCase = document.querySelector("#selected-case");
const views = [...document.querySelectorAll("[data-report-view]")];
const navigation = [...document.querySelectorAll('nav[aria-label="Report"] a')];
const overview = document.querySelector("#overview");
let currentView = overview;
let activeClaim = "";

function showComparison(announce = false, requestedClaim = "") {
  const selectedReviewers = reviewerInputs.filter(input => input.checked).map(input => input.value);
  reviewerCount.textContent = reviewerInputs.length ? `${selectedReviewers.length} / ${reviewerInputs.length}` : "";
  for (const action of reviewerActions) {
    const targetCount = action.dataset.selectReviewers === "all" ? reviewerInputs.length : 0;
    action.setAttribute("aria-disabled", String(selectedReviewers.length === targetCount));
  }
  const visible = selectedReviewers.map(provider =>
    panels.find(panel => panel.dataset.case === cases.value && panel.dataset.provider === provider)
  ).filter(Boolean);
  for (const panel of panels) {
    panel.hidden = !visible.includes(panel);
  }
  for (const panel of visible) grid.append(panel);
  for (const context of contexts) {
    const comparisonsHidden = context.classList.contains("case-issues") && !selectedReviewers.length;
    context.hidden = context.dataset.contextCase !== cases.value || comparisonsHidden;
  }
  for (const column of issueColumns) column.hidden = !selectedReviewers.includes(column.dataset.issueProvider);
  for (const hint of issueScrollHints) hint.hidden = selectedReviewers.length < 3;
  grid.dataset.visible = visible.length;
  empty.hidden = visible.length > 0;
  empty.textContent = !selectedReviewers.length && reviewerInputs.length
    ? "Select a reviewer to compare findings."
    : "No reviews are available for this selection.";
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
    const reviewer = reviewerInputs.find(input => input.value === panel.dataset.provider);
    if (reviewer) reviewer.checked = true;
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
  controls.addEventListener("change", () => showComparison(true));
  for (const action of reviewerActions) {
    action.addEventListener("click", () => {
      if (action.getAttribute("aria-disabled") === "true") return;
      for (const input of reviewerInputs) input.checked = action.dataset.selectReviewers === "all";
      showComparison(true);
    });
  }
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
