(() => {
  const targets = [...document.querySelectorAll("[data-range-title]")];
  if (!targets.length) return;
  const tip = document.createElement("div");
  tip.className = "data-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.append(tip);
  const part = (tag, className, text) => {
    if (!text) return null;
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = text;
    return node;
  };
  const place = (x, y) => {
    const margin = 8;
    const left = Math.min(Math.max(x - tip.offsetWidth / 2, margin), window.innerWidth - tip.offsetWidth - margin);
    const above = y - tip.offsetHeight - 14;
    tip.style.left = `${left}px`;
    tip.style.top = `${above < margin ? y + 22 : above}px`;
  };
  const rows = [...document.querySelectorAll(".interval-row")];
  const focusRow = (target, on) => {
    const row = target.closest(".interval-row");
    for (const other of rows) other.classList.toggle("is-dim", on && row !== null && other !== row);
  };
  const show = (target, x, y) => {
    const d = target.dataset;
    tip.replaceChildren(...[part("strong", "", d.rangeTitle), part("output", "", d.rangeMain),
      part("span", "tip-detail", d.rangeDetail), part("span", "tip-range", d.rangeRange), part("span", "tip-help", d.rangeHelp)]
      .filter(Boolean));
    tip.hidden = false;
    place(x, y);
    focusRow(target, true);
  };
  const hide = (target) => { tip.hidden = true; if (target) focusRow(target, false); };
  for (const target of targets) {
    if (!("rangeFocusOnly" in target.dataset)) {
      target.addEventListener("pointerenter", event => show(target, event.clientX, event.clientY));
      target.addEventListener("pointermove", event => place(event.clientX, event.clientY));
      target.addEventListener("pointerleave", () => hide(target));
    }
    target.addEventListener("focus", () => {
      const box = target.getBoundingClientRect();
      show(target, box.left + box.width / 2, box.top + box.height / 2);
    });
    target.addEventListener("blur", () => hide(target));
  }
  document.addEventListener("keydown", event => { if (event.key === "Escape") hide(); });
  window.addEventListener("scroll", () => hide(), { capture: true, passive: true });
})();

(() => {
  for (const button of document.querySelectorAll(".sha-copy")) {
    if (!navigator.clipboard) continue;
    button.hidden = false;
    const label = button.getAttribute("aria-label");
    let timer;
    button.addEventListener("click", async () => {
      clearTimeout(timer);
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        button.classList.add("is-copied");
        button.setAttribute("aria-label", "Commit ID copied");
      } catch {
        button.setAttribute("aria-label", "Copy failed. Open the commit on GitHub instead.");
      }
      timer = setTimeout(() => {
        button.classList.remove("is-copied");
        button.setAttribute("aria-label", label);
      }, 1600);
    });
  }
})();

(() => {
  const truncated = [...document.querySelectorAll("[data-truncate-tip]")];
  if (!truncated.length) return;
  const tip = document.createElement("p");
  tip.className = "text-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.append(tip);
  const hide = () => { tip.hidden = true; };
  const show = (element) => {
    if (element.scrollWidth <= element.clientWidth) return;
    tip.textContent = element.textContent;
    tip.hidden = false;
    const box = element.getBoundingClientRect();
    const margin = 8;
    const left = Math.min(Math.max(box.left, margin), window.innerWidth - tip.offsetWidth - margin);
    const above = box.top - tip.offsetHeight - 6;
    tip.style.left = `${left}px`;
    tip.style.top = `${above < margin ? box.bottom + 6 : above}px`;
  };
  for (const element of truncated) {
    const row = element.closest("li") || element;
    row.addEventListener("pointerenter", () => show(element));
    row.addEventListener("pointerleave", hide);
    const update = () => {
      const clipped = element.scrollWidth > element.clientWidth;
      if (clipped) row.tabIndex = 0;
      else row.removeAttribute("tabindex");
    };
    update();
    row.addEventListener("focus", () => show(element));
    row.addEventListener("blur", hide);
  }
  window.addEventListener("resize", hide);
  document.addEventListener("keydown", event => { if (event.key === "Escape") hide(); });
  window.addEventListener("scroll", hide, { capture: true, passive: true });
})();

(() => {
  const triggers = [...document.querySelectorAll(".info-trigger")];
  const templates = new Map([...document.querySelectorAll("template[data-info-template]")]
    .map(template => [template.dataset.infoTemplate, template]));
  if (!triggers.length || !HTMLElement.prototype.showPopover) return;

  const bubble = document.createElement("div");
  bubble.className = "info-bubble";
  bubble.setAttribute("popover", "manual");
  bubble.setAttribute("role", "dialog");
  bubble.tabIndex = -1;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "info-close";
  close.setAttribute("aria-label", "Close explanation");
  close.innerHTML = '<svg class="report-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true" focusable="false">' +
    '<path d="m4 4 8 8m0-8-8 8" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const content = document.createElement("div");
  content.className = "info-content";
  const arrow = document.createElement("span");
  arrow.className = "info-arrow";
  arrow.setAttribute("aria-hidden", "true");
  bubble.append(arrow, close, content);
  document.body.append(bubble);

  let active = null;

  function place() {
    if (!active) return;
    const margin = 8;
    const anchor = active.getBoundingClientRect();
    const width = bubble.offsetWidth;
    const height = bubble.offsetHeight;
    const below = window.innerHeight - anchor.bottom - margin;
    const above = anchor.top - margin;
    const side = below >= height + 10 || below >= above ? "below" : "above";
    const centre = anchor.left + anchor.width / 2;
    const left = Math.min(Math.max(centre - width / 2, margin), window.innerWidth - width - margin);
    const top = side === "below" ? anchor.bottom + 10 : anchor.top - height - 10;
    bubble.dataset.side = side;
    bubble.style.left = `${left}px`;
    bubble.style.top = `${Math.max(margin, top)}px`;
    bubble.style.setProperty("--arrow-x", `${Math.min(Math.max(centre - left, 18), width - 18)}px`);
  }

  function hide(restoreFocus = false) {
    if (!active) return;
    const trigger = active;
    active = null;
    trigger.setAttribute("aria-expanded", "false");
    bubble.hidePopover();
    if (restoreFocus) trigger.focus();
  }

  function show(trigger) {
    const template = templates.get(trigger.dataset.info);
    if (!template) return;
    if (active) active.setAttribute("aria-expanded", "false");
    content.replaceChildren(template.content.cloneNode(true));
    const title = content.querySelector(".info-title");
    if (title) {
      title.id = "info-bubble-title";
      bubble.setAttribute("aria-labelledby", title.id);
    }
    active = trigger;
    trigger.setAttribute("aria-expanded", "true");
    if (!bubble.matches(":popover-open")) bubble.showPopover();
    bubble.style.left = "0px";
    bubble.style.top = "0px";
    place();
    bubble.focus({ preventScroll: true });
  }

  for (const trigger of triggers) {
    trigger.hidden = false;
    trigger.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      if (active === trigger) hide(true);
      else show(trigger);
    });
  }
  close.addEventListener("click", () => hide(true));
  document.addEventListener("pointerdown", event => {
    if (active && !bubble.contains(event.target) && !active.contains(event.target)) hide();
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && active) {
      event.preventDefault();
      hide(true);
    }
  });
  window.addEventListener("resize", place);
  window.addEventListener("scroll", place, { capture: true, passive: true });
  window.addEventListener("hashchange", () => hide());
})();

(() => {
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
      tip.replaceChildren(...[title, context, value].filter(node => node.textContent));
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
})();
