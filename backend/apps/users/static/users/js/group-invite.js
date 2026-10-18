"use strict";

const inviteLink = document.querySelector("[data-invite-link]");
const copyButton = document.querySelector("[data-copy-invite]");
const shareButton = document.querySelector("[data-share-invite]");
const feedback = document.querySelector("[data-invite-feedback]");

if (inviteLink && copyButton && feedback) {
  const copyInviteLink = async () => {
    let copied = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(inviteLink.value);
        copied = true;
      }
    } catch {
      // Use the selection fallback when clipboard permission is unavailable.
    }
    if (!copied) {
      inviteLink.focus();
      inviteLink.select();
      copied = document.execCommand("copy");
    }
    feedback.textContent = copied
      ? "Enlace copiado."
      : "No se pudo copiar. Seleccioná el enlace y copialo manualmente.";
  };

  copyButton.addEventListener("click", copyInviteLink);

  if (shareButton && navigator.share) {
    shareButton.hidden = false;
    shareButton.addEventListener("click", async () => {
      try {
        await navigator.share({
          title: "Invitación a un grupo",
          text: "Te invito a unirte a este grupo de Plan B.",
          url: inviteLink.value,
        });
        feedback.textContent = "Enlace compartido.";
      } catch (error) {
        if (error.name !== "AbortError") {
          feedback.textContent = "No se pudo abrir la opción para compartir.";
        }
      }
    });
  }
}