"use strict";

// Mejoras opcionales: los formularios funcionan completos sin JavaScript.
document.querySelectorAll("[data-password-toggle]").forEach((button) => {
  const input = document.getElementById(button.dataset.passwordToggle);
  if (!input) return;
  button.hidden = false;
  const label = input.labels?.[0]?.textContent?.toLowerCase() || "contraseña";
  button.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.setAttribute("aria-pressed", String(show));
    button.setAttribute("aria-label", `${show ? "Ocultar" : "Mostrar"} ${label}`);
    button.textContent = show ? "Ocultar" : "Mostrar";
  });
});

document.querySelectorAll("[data-submit-form]").forEach((form) => {
  const button = form.querySelector("button[type='submit']");
  const label = button?.querySelector("[data-button-label]");
  const status = form.querySelector(".submit-status");
  if (!button || !label) return;
  const original = label.textContent;
  form.addEventListener("submit", (event) => {
    if (button.disabled) {
      event.preventDefault();
      return;
    }
    button.disabled = true;
    form.setAttribute("aria-busy", "true");
    label.textContent = button.dataset.loadingLabel;
    if (status) status.textContent = button.dataset.loadingLabel;
  });
  // El navegador puede restaurar una página con el botón previamente desactivado.
  window.addEventListener("pageshow", () => {
    button.disabled = false;
    form.removeAttribute("aria-busy");
    label.textContent = original;
    if (status) status.textContent = "";
  });
});

document.querySelector("[data-error-summary]")?.focus();
