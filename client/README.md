# JAMB Guide client

A Vite + React + TypeScript client for the FastAPI backend in this repository. Includes real account access, consent, streamed admissions conversations, source links, response feedback, conversation history and removal, university/course/cut-off/Post-UTME browsing, eligibility checks, profile updates, and account deletion.

## Development

Use Node 20.19+ or Node 22.12+ and a running backend:

```sh
# From the repository root, in one terminal:
DEBUG=true uvicorn app.main:app --reload

# In a second terminal:
npm --prefix client ci
npm --prefix client run dev


Open **http://127.0.0.1:5173**. Vite proxies `/api` and `/health` to `http://127.0.0.1:8000`, so local development does not require cross-origin browser requests. Set `VITE_API_PROXY_TARGET` in `client/.env.local` if the backend uses another port. See `.env.example`.

Do not copy the backend `.env` into the client. Vite environment values are public; provider keys and signing secrets belong exclusively to FastAPI.

## Production

```sh
npm --prefix client run build
DEBUG=false uvicorn app.main:app --host 0.0.0.0 --port 8000
```

FastAPI serves `client/dist` at `/` after the build, with `/api/v1` and `/health` still available. Restart FastAPI after creating the first production build. The repository Dockerfile also builds and bundles the Vite client; `docker compose up --build` serves the complete application on port 8000.

For separate static hosting, set `VITE_API_BASE_URL=https://your-api.example.com/api/v1` **before building** and add the frontend origin to the backend’s `ALLOWED_ORIGINS`. The proxy is a development-server feature, not part of the static output. Put HTTPS termination in front of the deployment.

## API behavior

The **Text appearance** button (the T icon in the header) offers Standard, Large,
and Extra large text, plus a Bold text option. These preferences apply across the
workspace and are saved on the current browser. The interface uses system fonts
and does not require an external font download.

- The browser stores access and refresh tokens in `sessionStorage`, scoped to the current tab; no profile or conversation bodies are cached persistently. Storage-blocked environments use in-memory tokens.
- Expired tokens refresh through a shared request to avoid concurrent rotation conflicts. Invalid sessions return to sign-in; transient network errors preserve the session.
- Delayed responses from a previous login are discarded. Delayed 401 responses retry with an already refreshed token without rotating again.
- SSE messages consume the backend’s `metadata`, JSON `delta`, `error`, and `[DONE]` frames. Missing completion frames are treated as interrupted responses. Stream cancellation is available while a response is running.
- Message history is fetched after a successful stream so source references, server IDs, finalized Gemini inline citations and feedback actions reflect persisted backend records. Google web sources and knowledge-base sources are labeled separately. Provider Search Suggestions render in a sandboxed iframe, with scripts and same-origin access disabled.
- Markdown is loaded only when messages are displayed. Raw HTML is disabled, and links permit only HTTP(S).
- Optional workspace screens are loaded on demand. No university records or AI responses are fabricated when the backend has no data.
- University name and institution-type filters work together. Empty pagination pages retain a Previous button, and inactive institutions and courses are excluded by the backend.
- Registration requires explicit processing consent. Existing accounts can accept consent before protected actions. Permanent account deletion requires typing `DELETE`.

## Checks

```sh
npm --prefix client run build
npm --prefix client test
```

Unit tests cover SSE framing/UTF-8, incomplete and failed streams, token refresh concurrency, invalid sessions, transient refresh errors and safe URLs.

Browser tests require a **disposable migrated backend database**, a running backend, and Vite proxying to it. They create test accounts, save data, submit feedback and delete successful test accounts. Do not target a production backend.

```sh
# Start the test backend and Vite in separate terminals with your test database.
DEBUG=true DATABASE_URL=postgresql+asyncpg://user:pass@localhost/jamb_test \
  uvicorn app.main:app --host 127.0.0.1 --port 18000
VITE_API_PROXY_TARGET=http://127.0.0.1:18000 npm --prefix client run dev

npm --prefix client run test:e2e
```

The test runner uses installed Google Chrome on macOS. On other platforms, install Chromium with `npx playwright install chromium` from `client`, or set `PLAYWRIGHT_CHROME_PATH`. Override the preview URL with `CLIENT_TEST_URL` when needed. Tests check desktop/mobile layouts, a 320px screen, source-independent local chat, account restoration, consent, feedback, profile persistence, eligibility empty states and sign-out. The welcome screen is also audited with axe for WCAG 2 A/AA and 2.1 AA rules; this automated audit is not a full accessibility certification.
