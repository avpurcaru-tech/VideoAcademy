"use strict";
const generationMessages = [
  { pattern: /\/lyrics\/(?:generate|regenerate)\/?$/, message: "Se generează versurile…" },
  { pattern: /\/music\/(?:generate|regenerate)\/?$/, message: "Se generează muzica…" },
  { pattern: /\/scenes\/[^/]+\/assets\/(?:generate|regenerate)\/?$/, message: "Se generează videoclipul…" },
  { pattern: /\/composition\/render\/?$/, message: "Se compune videoclipul final…" },
];

function showGenerationLoader(message, submitter) {
  if (document.getElementById("generation-loader")) return;
  const overlay = document.createElement("div");
  overlay.id = "generation-loader";
  overlay.className = "generation-loader";
  overlay.setAttribute("role", "status");
  overlay.setAttribute("aria-live", "assertive");
  overlay.setAttribute("aria-busy", "true");
  overlay.innerHTML = `<div class="generation-loader__panel"><span class="generation-loader__spinner" aria-hidden="true"></span><strong>${message}</strong><span>Te rugăm să nu închizi sau să reîncarci pagina.</span></div>`;
  document.body.appendChild(overlay);
  document.body.classList.add("is-generating");
  if (submitter) {
    submitter.disabled = true;
    submitter.setAttribute("aria-disabled", "true");
  }
}

document.addEventListener("submit", function (event) {
  const action = event.target instanceof HTMLFormElement ? event.target.action : "";
  const path = new URL(action, window.location.href).pathname;
  const operation = generationMessages.find(({ pattern }) => pattern.test(path));
  if (operation) showGenerationLoader(operation.message, event.submitter);
});

document.addEventListener("click", function (event) {
  const button = event.target.closest(".use-example");
  if (!button) return;
  const field = document.getElementById(button.dataset.exampleTarget);
  if (!field) return;
  field.value = button.dataset.exampleValue;
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.focus();
});
function filterCharacters() {
  const search = (document.getElementById("character-search")?.value || "").toLocaleLowerCase();
  const role = document.getElementById("character-role-filter")?.value || "";
  const status = document.getElementById("character-status-filter")?.value || "";
  document.querySelectorAll(".character-card").forEach(function (card) {
    card.hidden = !card.dataset.characterName.includes(search) || (role && card.dataset.characterRole !== role) || (status && card.dataset.characterStatus !== status);
  });
}
document.addEventListener("input", function (event) {
  if (["character-search", "character-role-filter", "character-status-filter"].includes(event.target.id)) filterCharacters();
  if (event.target.matches('input[name="selected_character_ids"]')) {
    const card = event.target.closest(".character-card");
    card?.classList.toggle("is-selected", event.target.checked);
    const selected = document.querySelectorAll('input[name="selected_character_ids"]:checked');
    if (selected.length === 1) {
      const primary = selected[0].closest(".character-card")?.querySelector('input[name="primary_character_id"]');
      if (primary) primary.checked = true;
    }
  }
});
