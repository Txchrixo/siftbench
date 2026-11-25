const controls = document.querySelector("#comparison-controls");
const cases = document.querySelector("#compare-case");
const reviewerInputs = [...controls.querySelectorAll('input[name="reviewer"]')];
const reviewerSelect = document.querySelector("#reviewer-select");
const reviewerTrigger = reviewerSelect.querySelector("summary");
const reviewerPreview = document.querySelector("#reviewer-preview");
const reviewerCount = document.querySelector("#reviewer-count");
const grid = document.querySelector(".comparison-grid");
const panels = [...grid.querySelectorAll("[data-case]")];
const contexts = [...document.querySelectorAll("[data-context-case]")];
const issueColumns = [...document.querySelectorAll("[data-issue-provider]")];
const referenceMatrix = document.querySelector("#reference-matrix");
const diffComments = [...document.querySelectorAll("[data-diff-provider]")];
const diffControls = [...document.querySelectorAll("[data-split-diff]")];
const comparisonView = document.querySelector("#comparison");
const comparisonTabs = comparisonView.querySelector(".comparison-tabs");
const tabs = [...comparisonTabs.querySelectorAll("a")];
const tabPanels = [...comparisonView.querySelectorAll("[data-comparison-view]")];
const fileCount = document.querySelector("#file-count");
const filesUnavailable = document.querySelector("#files-unavailable");
const problemsUnavailable = document.querySelector("#problems-unavailable");
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

function showReviewerSelection(selected) {
  const names = selected.slice(0, 2).map(input => input.labels[0].querySelector(".reviewer-name").cloneNode(true));
  for (const name of names) name.classList.add("reviewer-chip");
  reviewerPreview.replaceChildren(...names);
  if (!names.length) reviewerPreview.textContent = "Select reviewers";
  if (selected.length > names.length) {
    const more = document.createElement("span");
    more.className = "reviewer-more";
    more.textContent = `+${selected.length - names.length}`;
    more.setAttribute("aria-hidden", "true");
    reviewerPreview.append(more);
  }
  reviewerCount.textContent = reviewerInputs.length ? `${selected.length} / ${reviewerInputs.length}` : "";
  const previewNames = names.map(name => name.textContent).join(", ");
  reviewerTrigger.setAttribute("aria-label", `Select reviewers. ${selected.length} of ${reviewerInputs.length} selected. ${previewNames}`.trim());
}

function showComparison(announce = false, requestedClaim = "") {
  const selected = reviewerInputs.filter(input => input.checked);
  const selectedReviewers = selected.map(input => input.value);
  showReviewerSelection(selected);
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
  const currentFiles = contexts.find(context => context.hasAttribute("data-file-context") && !context.hidden);
  fileCount.textContent = currentFiles?.dataset.fileCount ?? "";
  fileCount.hidden = currentFiles?.dataset.fileCount === undefined;
  filesUnavailable.hidden = Boolean(currentFiles);
  problemsUnavailable.hidden = Boolean(cases.value && selectedReviewers.length);
  if (referenceMatrix) referenceMatrix.hidden = !selectedReviewers.length;
  for (const column of issueColumns) column.hidden = !selectedReviewers.includes(column.dataset.issueProvider);
  for (const comment of diffComments) comment.hidden = !selectedReviewers.includes(comment.dataset.diffProvider);
  for (const hint of issueScrollHints) hint.hidden = selectedReviewers.length < 3;
  grid.dataset.visible = visible.length;
  empty.hidden = visible.length > 0;
  empty.textContent = !cases.value ? "No case conditions were included in this report."
    : !selectedReviewers.length ? "Select a reviewer to compare findings."
    : "The selected reviewers have no planned attempts for this case.";
  const caseOption = cases.selectedOptions[0];
  selectedCase.textContent = caseOption?.dataset.title || "";
  selectedCase.hidden = !selectedCase.textContent;
  const links = visible.flatMap(panel => [...panel.querySelectorAll(".claim-index a")]);
  const active = links.find(link => link.hash === (requestedClaim || activeClaim));
  activeClaim = active?.hash || "";
  if (requestedClaim && active) active.closest(".claim-group").open = true;
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
      : empty.textContent;
  }
}

function showRoute(scroll = true, focus = true) {
  const target = document.getElementById(location.hash.slice(1));
  currentView = target?.closest("[data-report-view]") || (target?.id === "main" ? currentView : overview);
  if (currentView.id !== "comparison") reviewerSelect.open = false;
  for (const view of views) view.hidden = view !== currentView;
  for (const link of navigation) {
    if (link.hash === `#${currentView.id}`) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  }

  if (currentView === comparisonView) {
    const activePanel = target?.closest("[data-comparison-view]") || tabPanels[0];
    for (const tabPanel of tabPanels) tabPanel.hidden = tabPanel !== activePanel;
    for (const tab of tabs) {
      const selected = tab.hash === `#${activePanel.id}`;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
    if (target === activePanel) {
      const tab = tabs.find(item => item.hash === `#${activePanel.id}`);
      if (focus) tab.focus({ preventScroll: true });
      if (scroll) tab.scrollIntoView({ block: "nearest", inline: "nearest" });
      return;
    }
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
    if (focus) destination.focus({ preventScroll: true });
    destination.scrollIntoView({ block: "start" });
  }
}

comparisonTabs.setAttribute("role", "tablist");
for (const [index, tab] of tabs.entries()) {
  const panel = tabPanels[index];
  tab.setAttribute("role", "tab");
  tab.setAttribute("aria-controls", panel.id);
  panel.setAttribute("role", "tabpanel");
  panel.setAttribute("aria-labelledby", tab.id);
  panel.tabIndex = 0;
}
comparisonTabs.addEventListener("keydown", event => {
  const index = tabs.indexOf(event.target);
  if (index === -1) return;
  const next = {ArrowLeft: (index + tabs.length - 1) % tabs.length, ArrowRight: (index + 1) % tabs.length,
    Home: 0, End: tabs.length - 1}[event.key];
  if (next !== undefined) {
    event.preventDefault();
    for (const [position, tab] of tabs.entries()) tab.tabIndex = position === next ? 0 : -1;
    tabs[next].focus();
  } else if (event.key === " ") {
    event.preventDefault();
    tabs[index].click();
  }
});
comparisonTabs.hidden = !panels.length;

if (panels.length) {
  controls.hidden = false;
  controls.addEventListener("change", () => showComparison(true));
}

function setDiffView(split) {
  comparisonView.classList.toggle("show-split", split);
  for (const control of diffControls) control.checked = split;
}
for (const control of diffControls) {
  control.closest(".diff-toolbar").hidden = false;
  control.addEventListener("change", () => setDiffView(control.checked));
}
setDiffView(window.matchMedia("(min-width: 60rem)").matches);
for (const filter of document.querySelectorAll("[data-file-filter]")) {
  const caseFiles = filter.closest(".case-files");
  const files = [...caseFiles.querySelectorAll("[data-file-path]")];
  const emptyFiles = caseFiles.querySelector("[data-no-files]");
  filter.addEventListener("input", () => {
    const query = filter.value.trim().toLowerCase();
    for (const file of files) file.hidden = !file.dataset.filePath.toLowerCase().includes(query);
    emptyFiles.hidden = files.some(file => !file.hidden);
  });
}

reviewerSelect.addEventListener("toggle", () => {
  if (!reviewerSelect.open) return;
  const trigger = reviewerTrigger.getBoundingClientRect();
  const below = window.innerHeight - trigger.bottom;
  reviewerSelect.classList.toggle("opens-up", below < 280 && trigger.top > below);
});
reviewerSelect.addEventListener("keydown", event => {
  if (event.key !== "Escape" || !reviewerSelect.open) return;
  event.preventDefault();
  reviewerSelect.open = false;
  reviewerTrigger.focus();
});
reviewerSelect.addEventListener("focusout", event => {
  if (event.relatedTarget && !reviewerSelect.contains(event.relatedTarget)) reviewerSelect.open = false;
});
document.addEventListener("pointerdown", event => {
  if (!reviewerSelect.contains(event.target)) reviewerSelect.open = false;
});

document.addEventListener("click", event => {
  const link = event.target.closest('a[href^="#"]');
  if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  if (tabs.includes(link)) {
    event.preventDefault();
    if (link.hash !== location.hash) history.pushState(null, "", link.hash);
    showRoute(false);
  } else if (link.hash === location.hash) {
    event.preventDefault();
    showRoute();
  }
});
window.addEventListener("hashchange", () => showRoute());

for (const button of document.querySelectorAll(".prompt-copy")) button.hidden = false;
document.addEventListener("click", async event => {
  const button = event.target.closest(".prompt-copy");
  if (!button) return;
  const block = button.closest(".review-prompt");
  const code = block.querySelector("pre > code");
  const status = block.querySelector(".prompt-status");
  try {
    await navigator.clipboard.writeText(code.textContent);
    status.textContent = `${button.dataset.subject || "Prompt"} copied.`;
  } catch {
    code.parentElement.focus();
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(code);
    selection.removeAllRanges();
    selection.addRange(range);
    status.textContent = `${button.dataset.subject || "Prompt"} selected. Use your device’s copy command.`;
  }
});

const ask = document.querySelector("#ask-benchmark");
const dialog = document.querySelector("#benchmark-dialog");
if (typeof dialog?.showModal === "function") {
  const prompt = document.querySelector("#benchmark-prompt");
  const copyStatus = document.querySelector("#benchmark-copy-status");
  ask.hidden = false;
  ask.addEventListener("click", () => {
    copyStatus.textContent = "";
    dialog.showModal();
  });
  dialog.querySelector("#close-benchmark-dialog").addEventListener("click", () => dialog.close());
  dialog.querySelector("#copy-benchmark-prompt").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(prompt.value);
      copyStatus.textContent = "Prompt copied.";
    } catch {
      prompt.focus();
      prompt.select();
      copyStatus.textContent = "Text selected. Use your device’s copy command.";
    }
  });
}

for (const figure of document.querySelectorAll(".latency-figure")) {
  const tip = figure.querySelector(".latency-tip");
  const place = (x, y) => {
    const box = figure.getBoundingClientRect();
    const width = tip.offsetWidth;
    const left = Math.min(Math.max(x - box.left - width / 2, 0), box.width - width);
    const above = y - box.top - tip.offsetHeight - 14;
    tip.style.left = `${left}px`;
    tip.style.top = `${above < 0 ? y - box.top + 22 : above}px`;
  };
  const show = (point, x, y) => {
    const { tipTitle, tipContext, tipValue } = point.dataset;
    const title = document.createElement("strong");
    title.textContent = tipTitle;
    const context = document.createElement("span");
    context.textContent = tipContext;
    const value = document.createElement("output");
    value.textContent = tipValue;
    tip.replaceChildren(title, context, value);
    tip.hidden = false;
    place(x, y);
  };
  const hide = () => { tip.hidden = true; };
  for (const point of figure.querySelectorAll(".latency-point")) {
    point.addEventListener("pointerenter", event => show(point, event.clientX, event.clientY));
    point.addEventListener("pointermove", event => place(event.clientX, event.clientY));
    point.addEventListener("pointerleave", hide);
    point.addEventListener("focus", () => {
      const dot = point.querySelector(".latency-dot").getBoundingClientRect();
      show(point, dot.left + dot.width / 2, dot.top + dot.height / 2);
    });
    point.addEventListener("blur", hide);
  }
  figure.addEventListener("keydown", event => { if (event.key === "Escape") hide(); });
}

showComparison();
showRoute(Boolean(location.hash), false);
