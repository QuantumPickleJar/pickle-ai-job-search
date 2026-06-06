function cleanText(value) {
  return (value || "").replace(/\s+/g, " ").trim();
}

function limitText(value, maxLength) {
  const text = cleanText(value);
  return text.length > maxLength ? text.slice(0, maxLength) : text;
}

function firstText(selectors) {
  for (const selector of selectors) {
    const element = document.querySelector(selector);
    const text = element ? cleanText(element.innerText || element.textContent) : "";
    if (text) {
      return text;
    }
  }
  return "";
}

function visibleTextFromMain() {
  const main = document.querySelector("main") || document.body;
  return limitText(main ? main.innerText : document.body.innerText, 50000);
}

function extractDescription() {
  const selectors = [
    ".jobs-description__content .jobs-box__html-content",
    ".jobs-description__content",
    ".jobs-box__html-content",
    "#job-details",
    ".jobs-description-content__text",
    ".description__text",
    "[data-test-job-description]",
  ];
  const direct = firstText(selectors);
  if (direct) {
    return direct;
  }

  const raw = visibleTextFromMain();
  const marker = "About the job";
  const index = raw.toLowerCase().indexOf(marker.toLowerCase());
  return index >= 0 ? limitText(raw.slice(index + marker.length), 30000) : limitText(raw, 30000);
}

function extractLinkedInJob() {
  const title = firstText([
    ".job-details-jobs-unified-top-card__job-title",
    ".jobs-unified-top-card__job-title",
    ".top-card-layout__title",
    "h1",
  ]);

  const company = firstText([
    ".job-details-jobs-unified-top-card__company-name",
    ".jobs-unified-top-card__company-name",
    ".topcard__org-name-link",
    ".top-card-layout__card .topcard__flavor-row a",
    ".jobs-unified-top-card__primary-description-container a",
  ]);

  const location = firstText([
    ".job-details-jobs-unified-top-card__primary-description-container .tvm__text",
    ".jobs-unified-top-card__bullet",
    ".topcard__flavor--bullet",
    ".jobs-unified-top-card__workplace-type",
  ]);

  const description = extractDescription();
  const rawText = visibleTextFromMain();

  return {
    source: "linkedin",
    source_url: window.location.href,
    captured_at: new Date().toISOString(),
    title,
    company,
    location,
    description_text: description,
    raw_text: rawText,
    capture_method: "manual_extension_click",
  };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "CAPTURE_LINKEDIN_JOB") {
    return false;
  }

  try {
    sendResponse(extractLinkedInJob());
  } catch (error) {
    sendResponse({ error: error.message || "Unable to capture this LinkedIn job page." });
  }

  return false;
});
