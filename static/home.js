const hero = document.querySelector("#home-hero");

if (hero) {
  const slides = [
    {
      eyebrow: "FEATURE 01 · MOVEMENT CAPTURE",
      title: "Capture movement data with <em>every detail in view.</em>",
      description:
        "Bring acceleration, rotation, device ID, and capture time into one easy-to-review sample. Start a clearer data-collection workflow.",
      primary: "Capture a sample",
      primaryHref: "./dashboard.html",
      secondary: "Explore the workflow",
      secondaryHref: "./how-it-works.html",
      note: "Illustrative sensor animation only. This is not a live wearable connection.",
      visualLabel: "MOVEMENT SIGNAL",
      visualTitle: "Three-axis movement sample",
      visualFoot: "Illustrative signal animation",
      creative: "signal",
    },
    {
      eyebrow: "FEATURE 02 · BATCH IMPORT",
      title: "Have a dataset? Bring readings in <em>together.</em>",
      description:
        "Submit up to 500 timestamped sensor readings in one JSON batch. Each record is checked before the batch is saved.",
      primary: "Review batch import",
      primaryHref: "./dashboard.html",
      secondary: "See import requirements",
      secondaryHref: "./how-it-works.html",
      note: "Batch JSON API is available. Uploading files and device pairing are not included.",
      visualLabel: "BATCH DATA ACQUISITION",
      visualTitle: "A clearer way to import",
      visualFoot: "Illustrative import preview",
      creative: "batch",
    },
    {
      eyebrow: "FEATURE 03 · DATA QUALITY",
      title: "Check each reading before it is <em>stored.</em>",
      description:
        "Timezone-aware timestamps, complete sensor axes, and bounded heart-rate values help keep incoming records consistent.",
      primary: "See the validation rules",
      primaryHref: "./how-it-works.html",
      secondary: "Open the dashboard",
      secondaryHref: "./dashboard.html",
      note: "Validation checks data format only. It does not evaluate health or injury risk.",
      visualLabel: "READING VALIDATION",
      visualTitle: "Know what gets checked",
      visualFoot: "Illustrative validation flow",
      creative: "validation",
    },
  ];

  const eyebrow = document.querySelector("#hero-eyebrow");
  const title = document.querySelector("#hero-title");
  const description = document.querySelector("#hero-description");
  const primary = document.querySelector("#hero-primary");
  const secondary = document.querySelector("#hero-secondary");
  const note = document.querySelector("#hero-note-text");
  const visual = document.querySelector("#hero-visual");
  const visualLabel = visual.querySelector(".signal-label");
  const visualTitle = visual.querySelector(".signal-card-top strong");
  const visualFoot = visual.querySelector("#visual-foot");
  const slideCount = document.querySelector("#hero-slide-count");
  const indicators = [...document.querySelectorAll(".hero-indicator")];
  const pauseButton = document.querySelector("#hero-pause");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  let activeIndex = 0;
  let timer;
  let paused = reducedMotion.matches;
  let hovered = false;
  let focused = false;

  function renderSlide(index) {
    activeIndex = (index + slides.length) % slides.length;
    const slide = slides[activeIndex];
    hero.dataset.slide = String(activeIndex);
    hero.classList.remove("is-transitioning");
    void hero.offsetWidth;
    hero.classList.add("is-transitioning");
    eyebrow.textContent = slide.eyebrow;
    title.innerHTML = slide.title;
    description.textContent = slide.description;
    primary.href = slide.primaryHref;
    primary.innerHTML = `${slide.primary} <span aria-hidden="true">→</span>`;
    secondary.href = slide.secondaryHref;
    secondary.textContent = slide.secondary;
    note.textContent = slide.note;
    visualLabel.textContent = slide.visualLabel;
    visualTitle.textContent = slide.visualTitle;
    visualFoot.textContent = slide.visualFoot;
    visual.dataset.creative = slide.creative;
    slideCount.innerHTML = `${String(activeIndex + 1).padStart(2, "0")} <span>/ ${String(slides.length).padStart(2, "0")}</span>`;
    indicators.forEach((indicator, indicatorIndex) => {
      const selected = indicatorIndex === activeIndex;
      indicator.classList.toggle("is-active", selected);
      if (selected) indicator.setAttribute("aria-current", "true");
      else indicator.removeAttribute("aria-current");
    });
  }

  function stopTimer() {
    window.clearInterval(timer);
    timer = undefined;
  }

  function syncTimer() {
    stopTimer();
    pauseButton.textContent = paused ? "▶" : "Ⅱ";
    pauseButton.setAttribute(
      "aria-label",
      paused ? "Play automatic highlights" : "Pause automatic highlights",
    );
    if (!paused && !hovered && !focused && !document.hidden) {
      timer = window.setInterval(() => renderSlide(activeIndex + 1), 6500);
    }
  }

  document.querySelector("#hero-previous").addEventListener("click", () => {
    renderSlide(activeIndex - 1);
    syncTimer();
  });
  document.querySelector("#hero-next").addEventListener("click", () => {
    renderSlide(activeIndex + 1);
    syncTimer();
  });
  indicators.forEach((indicator) => {
    indicator.addEventListener("click", () => {
      renderSlide(Number(indicator.dataset.slideTo));
      syncTimer();
    });
  });
  pauseButton.addEventListener("click", () => {
    paused = !paused;
    syncTimer();
  });
  hero.addEventListener("mouseenter", () => {
    hovered = true;
    syncTimer();
  });
  hero.addEventListener("mouseleave", () => {
    hovered = false;
    syncTimer();
  });
  hero.addEventListener("focusin", () => {
    focused = true;
    syncTimer();
  });
  hero.addEventListener("focusout", (event) => {
    if (!hero.contains(event.relatedTarget)) {
      focused = false;
      syncTimer();
    }
  });
  document.addEventListener("visibilitychange", syncTimer);
  reducedMotion.addEventListener("change", (event) => {
    paused = event.matches;
    syncTimer();
  });

  syncTimer();
}
