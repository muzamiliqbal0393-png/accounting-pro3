/* ACCOUNTING PRO - UI behaviour */
(function () {
  "use strict";

  // ---- sidebar drawer (mobile) -------------------------------------------
  const body = document.body;
  const burger = document.querySelector(".hamburger");
  const backdrop = document.querySelector(".sidebar-backdrop");

  function closeSidebar() { body.classList.remove("sidebar-open"); }
  function toggleSidebar() { body.classList.toggle("sidebar-open"); }

  if (burger) burger.addEventListener("click", toggleSidebar);
  if (backdrop) backdrop.addEventListener("click", closeSidebar);
  document.querySelectorAll(".sidebar .nav-link").forEach(function (link) {
    link.addEventListener("click", function () {
      if (window.innerWidth < 992) closeSidebar();
    });
  });
  window.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeSidebar();
  });

  // ---- auto dismiss flash messages ---------------------------------------
  document.querySelectorAll("[data-autohide]").forEach(function (el) {
    setTimeout(function () {
      el.style.transition = "opacity .4s";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 420);
    }, 6000);
  });

  // ---- confirm dialogs ---------------------------------------------------
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) {
        e.preventDefault();
      }
    });
  });

  // ---- journal entry line calculator -------------------------------------
  const entryForm = document.getElementById("journal-form");
  if (entryForm) {
    const rows = function () {
      return Array.from(entryForm.querySelectorAll("[data-line]"));
    };

    function parse(v) {
      const n = parseFloat((v || "").toString().replace(/[^0-9.\-]/g, ""));
      return isNaN(n) ? 0 : n;
    }

    function recalc() {
      let debit = 0, credit = 0;
      rows().forEach(function (row) {
        const d = row.querySelector('[data-field="debit"]');
        const c = row.querySelector('[data-field="credit"]');
        // only one side may have a value
        if (d && c) {
          if (parse(d.value) > 0 && document.activeElement === d) c.value = "";
          if (parse(c.value) > 0 && document.activeElement === c) d.value = "";
        }
        debit += parse(d ? d.value : 0);
        credit += parse(c ? c.value : 0);
      });
      const fmt = function (n) {
        return "Rs. " + n.toLocaleString(undefined, {
          minimumFractionDigits: 2, maximumFractionDigits: 2
        });
      };
      const td = document.getElementById("total-debit");
      const tc = document.getElementById("total-credit");
      const diff = document.getElementById("total-diff");
      const status = document.getElementById("balance-status");
      if (td) td.textContent = fmt(debit);
      if (tc) tc.textContent = fmt(credit);
      if (diff) diff.textContent = fmt(debit - credit);
      if (status) {
        const ok = Math.abs(debit - credit) < 0.005 && debit > 0;
        status.className = "status-pill " + (ok ? "ok" : "bad");
        status.textContent = ok ? "Balanced - ready to post" :
          (debit === 0 && credit === 0 ? "Enter amounts" : "Not balanced");
      }
      const submitBtn = document.getElementById("post-btn");
      if (submitBtn) {
        const ok = Math.abs(debit - credit) < 0.005 && debit > 0;
        submitBtn.disabled = !ok;
        submitBtn.title = ok ? "" : "Total debit must equal total credit before posting";
      }
    }

    entryForm.addEventListener("input", recalc);
    entryForm.addEventListener("change", recalc);
    recalc();

    const addRow = document.getElementById("add-line");
    if (addRow) {
      addRow.addEventListener("click", function (e) {
        e.preventDefault();
        const table = document.getElementById("line-table");
        const last = table.querySelector("[data-line]:last-of-type");
        if (!last) return;
        const clone = last.cloneNode(true);
        const index = rows().length + 1;
        clone.querySelectorAll("input, select").forEach(function (input) {
          const base = input.name.replace(/_\d+$/, "");
          input.name = base + "_" + index;
          if (input.tagName === "SELECT") input.selectedIndex = 0;
          else input.value = "";
        });
        table.appendChild(clone);
        recalc();
      });
    }
  }

  // ---- account picker filter ---------------------------------------------
  document.querySelectorAll("[data-filter-source]").forEach(function (input) {
    const targetId = input.dataset.filterSource;
    input.addEventListener("input", function () {
      const term = input.value.toLowerCase();
      document.querySelectorAll("#" + targetId + " option").forEach(function (opt) {
        if (!opt.value) return;
        opt.hidden = term.length > 0 && opt.text.toLowerCase().indexOf(term) === -1;
      });
    });
  });

  // ---- print buttons -----------------------------------------------------
  document.querySelectorAll("[data-print]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      window.print();
    });
  });

  // ---- PWA registration --------------------------------------------------
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/service-worker.js").catch(function () {});
    });
  }
})();
