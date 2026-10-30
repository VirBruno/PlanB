"use strict";

(() => {
  const dialog = document.querySelector("[data-expenses-dialog]");
  const opener = document.querySelector("[data-open-expenses]");
  if (!dialog || !opener) return;
  const content = dialog.querySelector("[data-expenses-content]");
  const status = dialog.querySelector("[data-expenses-status]");
  const retry = dialog.querySelector("[data-reload-expenses]");
  let busy = false;
  let previousOverflow = "";

  function groupSelection() {
    const form = content.querySelector("[data-expense-form]");
    if (!form) return;
    const all = form.elements.all_group.checked;
    form.querySelectorAll("input[name='participants']").forEach(input => {
      input.disabled = all;
      if (all) input.checked = false;
    });
  }

  async function load(saved = false) {
    if (busy) return;
    busy = true;
    content.replaceChildren();
    content.setAttribute("aria-busy", "true");
    retry.hidden = true;
    status.textContent = saved ? "Gasto guardado. Actualizando…" : "Consultando gastos…";
    try {
      const response = await fetch(dialog.dataset.url, {credentials: "same-origin"});
      if (!response.ok || response.redirected) throw new Error();
      const html = await response.text();
      // HTML de un fragmento propio, escapado por Django; nunca datos de usuario como HTML.
      const fragment = document.createElement("template");
      fragment.innerHTML = html;
      if (!fragment.content.querySelector("[data-expense-summary]")) throw new Error();
      content.replaceChildren(fragment.content);
      document.querySelector("[data-expense-card-summary]").textContent =
        content.querySelector("[data-expense-summary]").dataset.expenseSummary;
      groupSelection();
      status.textContent = saved ? "Gasto guardado. Las cuentas están actualizadas." : "";
    } catch {
      status.textContent = saved
        ? "El gasto se guardó. No pudimos actualizar el resumen; actualizá los gastos para verlo."
        : "No pudimos consultar los gastos. Actualizá o volvé a iniciar sesión si venció.";
      retry.hidden = false;
    } finally {
      busy = false;
      content.removeAttribute("aria-busy");
    }
  }

  opener.addEventListener("click", () => {
    if (dialog.open) return;
    dialog.showModal();
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    load();
  });
  dialog.querySelector("[data-close-expenses]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    document.body.style.overflow = previousOverflow;
    opener.focus({preventScroll: true});
  });
  retry.addEventListener("click", () => load());
  content.addEventListener("change", event => {
    if (event.target.name === "all_group") groupSelection();
  });
  content.addEventListener("click", event => {
    const editor = content.querySelector("[data-expense-editor]");
    if (event.target.closest("[data-add-expense]")) {
      editor.hidden = false;
      content.querySelector("[data-expense-list]").hidden = true;
      editor.querySelector("input[name='description']").focus();
    }
    if (event.target.closest("[data-cancel-expense]")) {
      editor.hidden = true;
      content.querySelector("[data-expense-list]").hidden = false;
      content.querySelector("[data-add-expense]").focus();
    }
  });
  content.addEventListener("submit", async event => {
    const form = event.target.closest("[data-expense-form]");
    if (!form) return;
    event.preventDefault();
    if (busy) return;
    busy = true;
    const data = new FormData(form);
    const submit = form.querySelector("button[type='submit']");
    submit.disabled = true;
    form.setAttribute("aria-busy", "true");
    status.textContent = "Guardando gasto…";
    let saved = false;
    try {
      const response = await fetch(form.action, {method: "POST", body: data, credentials: "same-origin"});
      if (response.redirected) throw new Error();
      if (response.status === 400) {
        const editor = content.querySelector("[data-expense-editor]");
        editor.innerHTML = await response.text();
        groupSelection();
        editor.querySelector("[data-expense-errors]")?.focus();
        status.textContent = "Revisá los datos antes de guardar.";
      } else if (response.status === 201 && (await response.json()).saved === true) {
        saved = true;
      } else {
        throw new Error();
      }
    } catch {
      status.textContent = "No pudimos confirmar el guardado. Actualizá los gastos antes de volver a cargarlo.";
      // Un timeout puede ocurrir después de guardar en DB. No permitir un reenvío ciego.
      content.replaceChildren();
      retry.hidden = false;
    } finally {
      busy = false;
      submit.disabled = false;
      form.removeAttribute("aria-busy");
    }
    if (saved) await load(true);
  });
})();
