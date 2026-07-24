"use strict";
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
