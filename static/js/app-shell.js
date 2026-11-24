/* Shell helpers: Ctrl+K global search focus. Dropdowns come from Bootstrap. */
(function () {
  var input = document.getElementById("globalSearch");
  if (!input) return;

  document.addEventListener("keydown", function (e) {
    var tag = (document.activeElement && document.activeElement.tagName) || "";
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(tag) ||
                 (document.activeElement && document.activeElement.isContentEditable);
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      input.focus();
      input.select();
    } else if (e.key === "/" && !typing) {
      e.preventDefault();
      input.focus();
    } else if (e.key === "Escape" && document.activeElement === input) {
      input.blur();
    }
  });
})();
