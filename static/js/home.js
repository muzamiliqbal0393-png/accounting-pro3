/* ==========================================================================
   ACCOUNTING PRO — public home page behaviour
   New file. Small, dependency-free: hero background slider, mobile menu,
   contact-form validation check and a light scroll reveal.
   ========================================================================== */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ------------------------------ hero slider ---------------------------- */
  function initHero() {
    var hero = document.querySelector(".hero");
    var slides = Array.prototype.slice.call(document.querySelectorAll(".hero-slide"));
    var dots = Array.prototype.slice.call(document.querySelectorAll("#heroDots button"));
    if (!hero || slides.length < 2) return;

    var index = 0;
    var timer = null;
    var DWELL = 2500; // each background stays ~2.5 seconds (slower, easier to read)

    function show(next, restart) {
      index = (next + slides.length) % slides.length;
      slides.forEach(function (slide, i) {
        slide.classList.toggle("is-active", i === index);
      });
      dots.forEach(function (dot, i) {
        dot.classList.toggle("is-active", i === index);
        dot.setAttribute("aria-selected", i === index ? "true" : "false");
      });
      if (restart) start();
    }

    function start() {
      stop();
      if (reduceMotion) return;              // no autoplay for reduced motion
      timer = window.setInterval(function () { show(index + 1, false); }, DWELL);
    }
    function stop() {
      if (timer) { window.clearInterval(timer); timer = null; }
    }

    dots.forEach(function (dot, i) {
      dot.addEventListener("click", function () { show(i, true); });
    });

    // pause while the visitor is reading, resume afterwards
    hero.addEventListener("mouseenter", stop);
    hero.addEventListener("mouseleave", start);
    hero.addEventListener("focusin", stop);
    hero.addEventListener("focusout", start);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { stop(); } else { start(); }
    });

    // keyboard: left / right arrows move between backgrounds
    hero.addEventListener("keydown", function (event) {
      if (event.key === "ArrowRight") { show(index + 1, true); }
      if (event.key === "ArrowLeft") { show(index - 1, true); }
    });

    start();
  }

  /* --------------------------- mobile navigation ------------------------- */
  function initNav() {
    var burger = document.getElementById("hpBurger");
    var links = document.getElementById("hpNavLinks");
    if (!burger || !links) return;

    function close() {
      links.classList.remove("is-open");
      burger.setAttribute("aria-expanded", "false");
      burger.setAttribute("aria-label", "Open menu");
    }
    function toggle() {
      var open = links.classList.toggle("is-open");
      burger.setAttribute("aria-expanded", open ? "true" : "false");
      burger.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    }

    burger.addEventListener("click", function (event) {
      event.stopPropagation();
      toggle();
    });
    links.addEventListener("click", function (event) {
      if (event.target.closest("a")) close();
    });
    document.addEventListener("click", function (event) {
      if (!links.classList.contains("is-open")) return;
      if (!links.contains(event.target) && event.target !== burger) close();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") close();
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 900) close();
    });
  }

  /* ----------------------------- contact form ---------------------------- */
  function initContactForm() {
    var form = document.getElementById("contactForm");
    if (!form) return;

    form.addEventListener("submit", function (event) {
      if (!form.checkValidity()) {
        event.preventDefault();
        event.stopPropagation();
        var firstInvalid = form.querySelector(":invalid");
        if (firstInvalid) firstInvalid.focus();
        form.reportValidity();
      }
    });

    // keep the "valid" styling honest while typing
    form.querySelectorAll("input, textarea").forEach(function (field) {
      field.addEventListener("input", function () {
        if (field.validity.quitevalid !== undefined) return;
        field.classList.toggle("is-invalid", field.required && !field.checkValidity());
      });
    });
  }

  /* ---------------------------- scroll reveal ---------------------------- */
  function initReveal() {
    if (reduceMotion || !("IntersectionObserver" in window)) return;
    var targets = document.querySelectorAll(".hp-card, .hp-about-panel, .hp-why, .hp-fact");
    if (!targets.length) return;

    targets.forEach(function (el) {
      el.style.opacity = "0";
      el.style.transform = "translateY(14px)";
      el.style.transition = "opacity .5s ease, transform .5s ease";
    });

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.style.opacity = "1";
        entry.target.style.transform = "none";
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });

    targets.forEach(function (el) { observer.observe(el); });
  }

  function boot() {
    initHero();
    initNav();
    initContactForm();
    initReveal();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
