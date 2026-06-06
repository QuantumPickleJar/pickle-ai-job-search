# LinkedIn Job Clipper

Minimal Chrome/Edge extension for saving the currently open LinkedIn job posting to a configured `ai-job-search` service endpoint.

## What It Does

- Runs only when the user opens the extension popup.
- Captures visible fields from the current LinkedIn job page.
- Shows a preview before saving.
- Sends one JSON object to `<configured-service-base-url>/jobs/capture`.
- Supports per-browser saved settings for service base URL and API key.
- Stores only local extension settings (service URL and API key) in browser extension storage.

The extension stores only two local settings in browser extension storage:

- Service Base URL (for example `http://192.168.0.72:3927` or `https://raspberry-pi.<tailnet>.ts.net`)
- API key used as `X-API-Key` header for mutating endpoints.

Captured fields:

- `source`: `linkedin`
- `source_url`
- `title`
- `company`
- `location`
- `description_text`
- `raw_text`
- `capture_method`: `manual_extension_click`

## What It Does Not Do

- No LinkedIn search automation.
- No search result crawling.
- No infinite scrolling.
- No bulk capture.
- No "next job" automation.
- No Easy Apply automation.
- No recruiter, profile, or contact scraping.
- No automated messaging.
- No login or cookie extraction.
- No AI/model calls.
- No application submission.

## Configure Endpoint and API Key

Before saving jobs, open the extension popup and set:

1. **Service Base URL**
2. **API Key (X-API-Key)**
3. Click **Save Settings**

Examples:

```text
LAN:      http://192.168.0.72:3927
Tailnet:  https://raspberry-pi.<tailnet-name>.ts.net
Local:    http://localhost:3927
```

If `APP_API_KEY` is set on the service host, the API key field is required for saving jobs.

## Start The Local Intake Server

From the repository root:

```powershell
python scripts\job_intake_server.py
```

The simple intake server binds to localhost only:

```text
http://127.0.0.1:3927
```

Captured jobs are written to:

```text
job_intake/captured_jobs/
```

## Load In Chrome

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Click **Load unpacked**.
4. Select:

```text
extensions/linkedin-job-clipper/
```

## Load In Microsoft Edge

1. Open `edge://extensions`.
2. Enable Developer mode.
3. Click **Load unpacked**.
4. Select:

```text
extensions/linkedin-job-clipper/
```

## Test The Flow

1. Start the local intake server or run the full service container.
2. Open one LinkedIn job posting manually.
3. Click the LinkedIn Job Clipper extension icon.
4. Set **Service Base URL** and **API Key**, then click **Save Settings**.
5. Review the captured title, company, location, description character count, and URL.
6. Click **Save Job**.
7. Confirm the service reports a successful save.

For the simple localhost intake server, confirm a JSON file appears in:

```text
job_intake/captured_jobs/
```

## Extraction Heuristics

LinkedIn changes DOM structure often, so `content.js` tries several selector families for each field:

- Top-card job title selectors, then `h1`.
- Top-card company selectors.
- Top-card location/workplace selectors.
- Job description selectors such as `.jobs-description__content`, `.jobs-box__html-content`, and `#job-details`.
- Visible main-page text as a fallback.

If required fields are missing, the popup disables saving and shows which fields were not captured.

## CORS and Permissions

The simple intake server allows extension origins such as:

```text
chrome-extension://...
edge-extension://...
moz-extension://...
```

It does not use a wide-open `*` CORS policy.

The extension itself declares broad `http` and `https` host permissions so one build can target local, LAN, or tailnet endpoints without editing source files between environments.
