(function () {
  const REFRESH_INTERVAL_MS = 5000;

  function currentFragments() {
    return Array.from(document.querySelectorAll("[data-live-fragment='1']"));
  }

  function badgeSnapshots(fragment) {
    return Array.from(fragment.querySelectorAll("[data-status-badge='1']")).map(function (badge) {
      const style = window.getComputedStyle(badge);
      return {
        state: badge.getAttribute("data-badge-state") || "",
        background: style.backgroundColor,
        border: style.borderColor,
        color: style.color,
      };
    });
  }

  function animateChangedBadges(fragment, previousBadges) {
    const nextBadges = Array.from(fragment.querySelectorAll("[data-status-badge='1']"));
    nextBadges.forEach(function (badge, index) {
      const previous = previousBadges[index];
      if (!previous) {
        return;
      }
      const nextState = badge.getAttribute("data-badge-state") || "";
      if (!nextState || nextState === previous.state) {
        return;
      }
      const style = window.getComputedStyle(badge);
      badge.style.setProperty("--badge-from-background", previous.background);
      badge.style.setProperty("--badge-from-border", previous.border);
      badge.style.setProperty("--badge-from-color", previous.color);
      badge.style.setProperty("--badge-to-background", style.backgroundColor);
      badge.style.setProperty("--badge-to-border", style.borderColor);
      badge.style.setProperty("--badge-to-color", style.color);
      badge.classList.remove("badge-transition-enter");
      void badge.offsetWidth;
      badge.classList.add("badge-transition-enter");
      badge.addEventListener(
        "animationend",
        function () {
          badge.classList.remove("badge-transition-enter");
        },
        { once: true }
      );
    });
  }

  async function refreshFragments() {
    const fragments = currentFragments();
    if (fragments.length === 0 || document.hidden) {
      return;
    }

    const url = window.location.pathname + window.location.search;
    const response = await fetch(url, {
      cache: "no-store",
      headers: { "X-Requested-With": "live-refresh" },
    });
    if (!response.ok) {
      return;
    }

    const html = await response.text();
    const parsed = new DOMParser().parseFromString(html, "text/html");

    for (const fragment of fragments) {
      if (!fragment.id) {
        continue;
      }
      const previousBadges = badgeSnapshots(fragment);
      const next = parsed.getElementById(fragment.id);
      if (!next) {
        continue;
      }
      fragment.innerHTML = next.innerHTML;
      animateChangedBadges(fragment, previousBadges);
    }
  }

  function startPolling() {
    if (currentFragments().length === 0) {
      return;
    }

    setInterval(function () {
      refreshFragments().catch(function () {
        // Keep polling even if one refresh cycle fails.
      });
    }, REFRESH_INTERVAL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startPolling);
  } else {
    startPolling();
  }
})();
