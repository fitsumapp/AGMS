/**
 * AGMS Form Collapsible Accordion Controller
 * Keeps all form section cards and inline formsets collapsed by default.
 * Clicking the header or chevron icon expands/collapses the section.
 */
document.addEventListener("DOMContentLoaded", function () {
  function initCollapsibleCards() {
    // Target both ModelAdmin fieldsets and inline formsets on change forms
    var form = document.querySelector(".change-form form");
    if (!form) return;

    var fieldsets = form.querySelectorAll("fieldset.module, .inline-group fieldset.module, .tabular.inline-related fieldset.module");

    fieldsets.forEach(function (fieldset, index) {
      // Find the header element
      var header = fieldset.querySelector("h2, .inline-heading");
      if (!header) return;

      // Ensure cursor indicates interactivity
      header.style.cursor = "pointer";

      // Identify the content body to collapse/expand
      // In fieldset: .form-rows, or all siblings of h2
      // In inline tabular: table / .tabular_table, form-actions, or sibling divs
      var contentElements = [];
      var siblings = Array.from(fieldset.children);
      siblings.forEach(function (el) {
        if (el !== header && !el.matches("h2") && !el.matches("summary") && !el.matches("input[type=hidden]")) {
          contentElements.push(el);
        }
      });

      // Also check if fieldset is inside an inline group with table outside fieldset
      var inlineGroup = fieldset.closest(".inline-group, .inline-related, [data-inline-type]");
      if (inlineGroup) {
        var extraSiblings = Array.from(inlineGroup.children).filter(function (child) {
          return child !== fieldset && !child.matches("input[type=hidden]");
        });
        contentElements = contentElements.concat(extraSiblings);
      }

      // Check if there are validation errors inside this section
      var hasErrors = fieldset.querySelector(".errors, .errorlist, .errornote, [aria-invalid='true']") !== null;

      // Default state: collapsed unless it contains errors
      var isExpanded = false;
      if (hasErrors) {
        isExpanded = true;
      }

      function updateDisplay() {
        contentElements.forEach(function (el) {
          el.style.display = isExpanded ? "" : "none";
        });
        if (isExpanded) {
          fieldset.classList.remove("erp-collapsed");
          fieldset.classList.add("erp-expanded");
        } else {
          fieldset.classList.remove("erp-expanded");
          fieldset.classList.add("erp-collapsed");
        }
      }

      // Initial apply
      updateDisplay();

      // Click handler on header
      header.addEventListener("click", function (e) {
        // Toggle state
        isExpanded = !isExpanded;
        updateDisplay();
      });
    });
  }

  // Run on initial page load
  initCollapsibleCards();

  // Also support HTMX or dynamic DOM updates
  document.body.addEventListener("htmx:afterSwap", initCollapsibleCards);
});
