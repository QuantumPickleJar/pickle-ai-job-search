(function () {
  const REFRESH_INTERVAL_MS = 5000;

  function currentFragments() {
    return Array.from(document.querySelectorAll("[data-live-fragment='1']"));
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
      const next = parsed.getElementById(fragment.id);
      if (!next) {
        continue;
      }
      fragment.innerHTML = next.innerHTML;
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
