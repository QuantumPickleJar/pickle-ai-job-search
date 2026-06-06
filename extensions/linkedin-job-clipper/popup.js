const DEFAULT_BASE_URL = "http://localhost:3927";
const SETTINGS_KEY = "intakeSettings";

const elements = {
  title: document.getElementById("title"),
  company: document.getElementById("company"),
  location: document.getElementById("location"),
  descriptionCount: document.getElementById("descriptionCount"),
  sourceUrl: document.getElementById("sourceUrl"),
  serviceBaseUrl: document.getElementById("serviceBaseUrl"),
  apiKey: document.getElementById("apiKey"),
  saveSettingsButton: document.getElementById("saveSettingsButton"),
  status: document.getElementById("status"),
  saveButton: document.getElementById("saveButton"),
};

let capturedJob = null;
let intakeSettings = {
  baseUrl: DEFAULT_BASE_URL,
  apiKey: "",
};

function normalizeBaseUrl(value) {
  const trimmed = String(value || "").trim();
  if (!trimmed) {
    return DEFAULT_BASE_URL;
  }

  let url;
  try {
    url = new URL(trimmed);
  } catch {
    throw new Error("Service URL must be a valid absolute URL, for example http://192.168.0.72:3927");
  }

  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("Service URL must use http or https.");
  }
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Service URL must be only scheme + host + optional port.");
  }
  url.pathname = "";
  return url.toString().replace(/\/$/, "");
}

function intakeUrlFrom(settings) {
  return `${settings.baseUrl}/jobs/capture`;
}

function updateSettingsUi() {
  elements.serviceBaseUrl.value = intakeSettings.baseUrl;
  elements.apiKey.value = intakeSettings.apiKey;
}

async function loadSettings() {
  const stored = await chrome.storage.local.get(SETTINGS_KEY);
  const raw = stored[SETTINGS_KEY] || {};
  intakeSettings = {
    baseUrl: normalizeBaseUrl(raw.baseUrl || DEFAULT_BASE_URL),
    apiKey: String(raw.apiKey || "").trim(),
  };
  updateSettingsUi();
}

async function saveSettings() {
  const normalized = normalizeBaseUrl(elements.serviceBaseUrl.value);
  const nextSettings = {
    baseUrl: normalized,
    apiKey: String(elements.apiKey.value || "").trim(),
  };
  await chrome.storage.local.set({ [SETTINGS_KEY]: nextSettings });
  intakeSettings = nextSettings;
  updateSettingsUi();
  setStatus(`Settings saved. Capture target: ${intakeSettings.baseUrl}`);
}

function setStatus(message, kind = "") {
  elements.status.textContent = message;
  elements.status.className = `status ${kind}`.trim();
}

function setText(element, value, fallback = "Not found") {
  element.textContent = value && String(value).trim() ? value : fallback;
}

function renderCapture(job) {
  setText(elements.title, job.title);
  setText(elements.company, job.company);
  setText(elements.location, job.location);
  setText(elements.sourceUrl, job.source_url);
  elements.descriptionCount.textContent = String((job.description_text || "").length);

  const missing = [];
  for (const field of ["title", "company", "description_text"]) {
    if (!job[field] || !String(job[field]).trim()) {
      missing.push(field);
    }
  }

  if (missing.length > 0) {
    elements.saveButton.disabled = true;
    setStatus(`Capture incomplete. Missing: ${missing.join(", ")}.`, "error");
    return;
  }

  elements.saveButton.disabled = false;
  setStatus("Preview captured. Review the fields, then save to the configured service.");
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

async function captureCurrentJob() {
  const tab = await getActiveTab();
  if (!tab || !tab.id) {
    throw new Error("No active tab found.");
  }

  if (!tab.url || !/^https:\/\/(www\.)?linkedin\.com\/jobs\//.test(tab.url)) {
    throw new Error("Open a LinkedIn job page before using the clipper.");
  }

  return chrome.tabs.sendMessage(tab.id, { type: "CAPTURE_LINKEDIN_JOB" });
}

async function saveCapturedJob() {
  if (!capturedJob) {
    return;
  }

  elements.saveButton.disabled = true;
  const intakeUrl = intakeUrlFrom(intakeSettings);
  setStatus(`Saving to ${intakeUrl} ...`);

  try {
    const headers = {
      "Content-Type": "application/json",
    };
    if (intakeSettings.apiKey) {
      headers["X-API-Key"] = intakeSettings.apiKey;
    }

    const response = await fetch(intakeUrl, {
      method: "POST",
      headers,
      body: JSON.stringify(capturedJob),
    });

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        payload.detail || payload.error || `Service returned HTTP ${response.status}.`
      );
    }

    setStatus(`Saved: ${payload.path || payload.id || "captured job"}`, "success");
  } catch (error) {
    elements.saveButton.disabled = false;
    const message =
      error instanceof TypeError
        ? `Service is not reachable at ${intakeSettings.baseUrl}. Verify LAN/tailnet routing and service health.`
        : error.message;
    setStatus(`Save failed: ${message}`, "error");
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await loadSettings();
    capturedJob = await captureCurrentJob();
    if (!capturedJob || capturedJob.error) {
      throw new Error(capturedJob && capturedJob.error ? capturedJob.error : "Capture failed.");
    }
    renderCapture(capturedJob);
  } catch (error) {
    capturedJob = null;
    elements.saveButton.disabled = true;
    setStatus(error.message, "error");
  }
});

elements.saveButton.addEventListener("click", saveCapturedJob);
elements.saveSettingsButton.addEventListener("click", async () => {
  try {
    await saveSettings();
  } catch (error) {
    setStatus(`Settings failed: ${error.message}`, "error");
  }
});
