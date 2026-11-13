"use strict";

(() => {
  const root = document.querySelector("[data-confirmed-layout]");
  if (!root || root.dataset.workspaceInitialized) return;
  root.dataset.workspaceInitialized = "true";
  const controls = [...root.querySelectorAll("[data-tool]")];
  const panels = [...root.querySelectorAll("[data-tool-panel]")];
  const drawer = root.querySelector("[data-plan-drawer]");
  const openers = [...root.querySelectorAll("[data-open-plan-details]")];
  const labels = new Map(controls.map(button => [button, button.querySelector(".pb-tool-action").textContent]));
  let active = null;
  let returnFocus = null;
  let previousOverflow = null;
  let drawerCloseTimer = null;

  // Animar solo la presentacion: hidden/open, eventos y foco conservan su logica.
  const reveals = new WeakMap();
  function revealDown(element, startHeight = 0) {
    reveals.get(element)?.cancel();
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || !element.animate) return;
    const height = element.getBoundingClientRect().height;
    reveals.set(element, element.animate([
      {height: `${startHeight}px`, opacity: .4, overflow: "clip"},
      {height: `${height}px`, opacity: 1, overflow: "clip"}
    ], {duration: 280, easing: "cubic-bezier(.22, 1, .36, 1)"}));
  }
  root.addEventListener("toggle", event => {
    const details = event.target;
    if (details.tagName === "DETAILS" && details.open && details.getClientRects().length) {
      revealDown(details, details.querySelector("summary")?.getBoundingClientRect().height || 0);
    }
  }, true);

  function selectTool(name, focus = false) {
    if (active === "gastos" && name !== "gastos") {
      const url = new URL(location.href);
      if (url.searchParams.get("open") === "expenses") {
        url.searchParams.delete("open");
        history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
      }
    }
    active = name;
    controls.forEach(button => {
      const expanded = button.dataset.tool === name;
      button.setAttribute("aria-expanded", String(expanded));
      button.querySelector(".pb-tool-action").textContent = expanded ? "Cerrar ×" : labels.get(button);
    });
    panels.forEach(panel => {
      const open = panel.dataset.toolPanel === name;
      panel.hidden = !open;
      if (open) {
        panel.dispatchEvent(new CustomEvent("planb:tool-open", {bubbles: true}));
        revealDown(panel);
      } else reveals.get(panel)?.cancel();
    });
    if (name === "clima") root.querySelector("[data-weather-compare]")?.setAttribute("open", "");
    if (focus) controls.find(button => button.dataset.tool === name)?.focus({preventScroll: true});
  }
  controls.forEach(button => button.addEventListener("click", () => selectTool(active === button.dataset.tool ? null : button.dataset.tool)));
  root.querySelectorAll("[data-close-tool]").forEach(button => button.addEventListener("click", () => {
    const previous = active;
    selectTool(null);
    controls.find(control => control.dataset.tool === previous)?.focus({preventScroll: true});
  }));
  root.addEventListener("planb:tool-close", () => {
    selectTool(null);
    root.querySelector('[data-tool="gastos"]')?.focus({preventScroll: true});
    const url = new URL(location.href);
    if (url.searchParams.get("open") === "expenses") {
      url.searchParams.delete("open");
      history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    }
  });

  function openDrawer(section, opener) {
    window.clearTimeout(drawerCloseTimer);
    drawer.classList.remove("is-closing");
    if (!drawer.open) {
      restoreDrawer();
      returnFocus = opener || openers[0];
      previousOverflow = document.body.style.overflow;
      drawer.showModal();
      document.body.style.overflow = "hidden";
      openers.forEach(button => button.setAttribute("aria-expanded", "true"));
    }
    if (section) {
      const block = [...drawer.querySelectorAll("[data-decision-block]")].find(node => node.dataset.decisionBlock === section);
      if (block) block.open = true;
      const heading = drawer.querySelector("#decision-history-title");
      heading.focus({preventScroll: true});
      heading.scrollIntoView({block: "start", behavior: "instant"});
    } else drawer.querySelector("[data-close-plan-details]").focus({preventScroll: true});
  }
  function finishDrawerClose() {
    if (drawer.classList.contains("is-closing")) {
      drawer.close();
      restoreDrawer();
    }
  }
  function closeDrawer() {
    if (!drawer.open || drawer.classList.contains("is-closing")) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      drawer.close();
      restoreDrawer();
      return;
    }
    drawer.classList.add("is-closing");
    // Respaldo si el navegador no emite animationend o la animación se cancela.
    drawerCloseTimer = window.setTimeout(finishDrawerClose, 350);
  }
  drawer.addEventListener("animationend", event => {
    if (event.target === drawer && event.animationName === "pb-plan-drawer-exit") finishDrawerClose();
  });
  drawer.addEventListener("cancel", event => { event.preventDefault(); closeDrawer(); });
  openers.forEach(button => button.addEventListener("click", () => openDrawer(button.dataset.decisionTarget, button)));
  drawer.querySelector("[data-close-plan-details]").addEventListener("click", closeDrawer);
  function restoreDrawer() {
    window.clearTimeout(drawerCloseTimer);
    drawer.classList.remove("is-closing");
    if (previousOverflow === null) return;
    document.body.style.overflow = previousOverflow;
    previousOverflow = null;
    openers.forEach(button => button.setAttribute("aria-expanded", "false"));
    returnFocus?.focus({preventScroll: true});
  }
  // Un close nativo puede llegar después de una reapertura en el mismo frame.
  drawer.addEventListener("close", () => { if (!drawer.open) restoreDrawer(); });
  drawer.addEventListener("click", event => {
    const rect = drawer.getBoundingClientRect();
    if (event.target === drawer && (event.clientX < rect.left || event.clientX > rect.right
        || event.clientY < rect.top || event.clientY > rect.bottom)) closeDrawer();
  });
  drawer.addEventListener("keydown", event => {
    if (event.target.closest("dialog") !== drawer) return;
    if (event.key === "Escape") { event.preventDefault(); closeDrawer(); return; }
    if (event.key !== "Tab") return;
    const focusable = [...drawer.querySelectorAll('button:not(:disabled),a[href],summary,input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')]
      .filter(node => node.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && (document.activeElement === first || !focusable.includes(document.activeElement))) {
      event.preventDefault(); last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  });

  // Los dialogs internos siguen siendo modales independientes del drawer cerrado.
  const ideal = root.querySelector("[data-ideal-dialog]");
  if (ideal) {
    root.append(ideal);
    ideal.addEventListener("close", () => openDrawer("voting"));
  }
  function deepLink() {
    const hashes = {"#clima": "clima", "#gastos": "gastos", "#sugerencias": "sugerencias"};
    const tool = hashes[location.hash] || (new URL(location.href).searchParams.get("open") === "expenses" ? "gastos" : null);
    if (tool) selectTool(tool);
    else if (location.hash === "#decision") openDrawer("result");
    else if (location.hash === "#proposal-election") openDrawer("method");
    else if (location.hash === "#proposal-combat") openDrawer("voting");
    else if (location.hash === "#proposals-title") openDrawer("proposals");
  }
  window.addEventListener("hashchange", deepLink);
  document.addEventListener("DOMContentLoaded", deepLink, {once: true});

  // Estado ya disponible en el DOM: ninguna consulta adicional para la tarjeta.
  const recommendations = root.querySelector("[data-recommendations]");
  const cardStatus = root.querySelector("[data-ia-tool-status]");
  if (recommendations && cardStatus) {
    const updateStatus = () => {
      const state = recommendations.querySelector("[data-suggestions-feedback]")?.dataset.state;
      const count = recommendations.querySelectorAll("[data-suggestions-list] > li").length;
      cardStatus.textContent = ["loading", "pending"].includes(state) ? "Generando sugerencias…"
        : state === "ready" ? `${count} ${count === 1 ? "lugar disponible" : "lugares disponibles"}`
          : count ? "Última versión disponible" : "No disponible por ahora";
    };
    new MutationObserver(updateStatus).observe(recommendations, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["data-state"]});
    updateStatus();
  }
})();
