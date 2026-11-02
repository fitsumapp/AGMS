/**
 * AGMS Form Collapsible Accordion Controller
 * Integrates with native <details>/<summary> elements created by Django/Unfold.
 */
document.addEventListener("DOMContentLoaded", function () {
  function initCollapsibleCards() {
    var form = document.querySelector(".change-form form");
    if (!form) return;

    // Handle all <details> elements in fieldsets and inlines
    var detailsList = form.querySelectorAll("fieldset.module details");

    detailsList.forEach(function (details) {
      // Check if there are errors inside
      var hasErrors = details.querySelector(".errors, .errorlist, .errornote, [aria-invalid='true']") !== null;

      if (!hasErrors) {
        details.removeAttribute("open");
      } else {
        details.setAttribute("open", "");
      }
    });
  }

  initCollapsibleCards();
  document.body.addEventListener("htmx:afterSwap", initCollapsibleCards);
});
