(() => {
  const reviewer = document.querySelector("#coverage-reviewer");
  const changes = document.querySelector("#coverage-changes");
  const status = document.querySelector("#coverage-filter-status");
  const panels = [...document.querySelectorAll("[data-reviewer-index]")];

  function showCoverage(announce) {
    for (const panel of panels) {
      panel.hidden = panel.dataset.reviewerIndex !== reviewer.value;
      if (panel.hidden) continue;
      const rows = [...panel.querySelectorAll(".sample-row")];
      for (const row of rows) {
        row.hidden = changes.checked && row.dataset.changed !== "true";
      }
      const visible = rows.filter((row) => !row.hidden).length;
      panel.querySelector(".sample-empty").hidden = visible > 0;
      if (announce) status.textContent = `${visible} matching pull requests`;
    }
  }

  reviewer.addEventListener("change", () => showCoverage(true));
  changes.addEventListener("change", () => showCoverage(true));
  showCoverage(false);
  document.querySelector(".coverage-filters").hidden = false;
})();
