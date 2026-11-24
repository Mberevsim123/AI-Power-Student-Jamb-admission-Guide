# JAMB Guide backend

FastAPI backend for the JAMB and Nigerian university admissions chatbot described in the supplied project document. Provides registration guidance, institution and course lookups, cut-off marks, O’Level/UTME eligibility checks, Post-UTME schedules, authenticated conversations, streaming, feedback, and administrator source maintenance.

## Web client

The Vite + React + TypeScript client lives in [`client/`](client/README.md). It connects to the backend for accounts, streaming chat, history, feedback, university information, eligibility and student preferences. Run `npm --prefix client ci` and `npm --prefix client run dev` alongside FastAPI for development. A production build (`npm --prefix client run build`) is served by FastAPI at `/`; the Dockerfile bundles both applications. See the [client setup guide](client/README.md) for proxy configuration and browser tests.

## Development setup

Use Python **3.12** (especially on Intel macOS). Supply provider keys in the backend `.env`. Search-enabled chats try Gemini first when it is configured; otherwise the configured provider order applies. Missing/placeholder keys are skipped. Each provider has its own model setting, so a Groq model name is never sent to Google or another provider. Provider access, billing and quotas remain subject to the relevant account.

Embeddings run locally with [FastEmbed](https://qdrant.tech/documentation/fastembed/) and `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions). Model weights download on first use; no paid embedding key is required. PostgreSQL, Qdrant and Redis run locally. The first embedding request is slower while the model downloads and loads.

```sh
cp .env.example .env  # only for a new installation; keep existing credentials
```

Set a random `SECRET_KEY` and your `GROQ_API_KEY` in `.env`. Existing installations should update provider/model settings rather than overwrite their `.env`.

With Docker running:

```sh
docker compose up --build -d
```

In the Compose stack, the API container applies migrations and loads the university directory before it starts. Open http://localhost:8000/docs (`DEBUG=true`). Services bind to localhost for development. The bundled database password is for local development only. Production needs its own secrets, HTTPS termination, access controls and capacity testing.

For a host Python process:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
docker compose up -d postgres redis qdrant
DEBUG=true alembic upgrade head
DEBUG=true uvicorn app.main:app --reload
```

Environment variables take precedence over `.env`; the API accepts boolean DEBUG values and the IDE build labels `debug`/`release`.

## Accounts and consent

`POST /api/v1/auth/register` requires `email`, `username`, `password`, and `data_consent: true`. This records consent to storing account/profile/chat data and sending conversational context to configured AI providers, including fallback providers when an earlier attempt fails. Passwords are hashed; provide passwords of 8–72 UTF-8 bytes. Sign in with `POST /api/v1/auth/login`; use the returned access token as a Bearer token. Refresh tokens rotate on `/auth/refresh`. Logout revokes refresh tokens; existing access tokens remain valid until expiry (30 minutes by default).

Existing accounts must call `POST /api/v1/auth/consent` with `{"data_consent":true}` before chat or profile operations. `GET /auth/me` returns the account. `DELETE /auth/me` permanently removes the account and dependent profile, conversations, messages, feedback and refresh tokens. Conversation deletion alone hides it from history; it does not erase records.

After registering a consenting account, grant the administrator role from the application environment:

```sh
python -m scripts.create_admin student@example.com
# Docker alternative:
docker compose exec api python -m scripts.create_admin student@example.com
```

No default administrator or password is created.

## Loading verified information

The application bundles six short, sourced JAMB process guides and the full NUC directory of approved universities ([`app/data/nuc_universities.csv`](app/data/nuc_universities.csv): 77 federal, 69 state and 183 private, captured 24 November 2026), reviewed against [JAMB guidance](https://jamb.gov.ng/FAQ), [CAPS](https://jamb.gov.ng/caps), [JAMB support](https://support.jamb.gov.ng/faqs) and the [NUC directory](https://www.nuc.edu.ng/nigerian-univerisities/state-univerisity/). These work without AI keys, paid embeddings or Qdrant. They cover institution identities and general processes, not a complete national admissions dataset.

The NUC tables have no state column, so each university's state is assigned from its location (one institution, Crestfield University, is `Unspecified`). Obvious NUC typos are corrected and the original spelling is kept in `nuc_name`. The chat recognises every listed university by its full name, common short name or abbreviation, and answers directory questions such as "private universities in Ogun" or "how many federal universities are there?" without a model. Load the directory into the university API with `python -m scripts.seed_knowledge`. The command is idempotent and preserves existing administrator records. It creates no cut-off marks, admission cycles or course requirements.

The API starts with **no fabricated admissions records**. The project document specifies the software, but is not an official source for admissions facts. Administrators must review official JAMB or university publications and maintain their data for each admission cycle.

Use `/docs` and the administrator’s Bearer token to populate, in order:

1. `POST /api/v1/admin/universities` — institution name, state, type and website.
2. `POST /api/v1/admin/cycles` — year, current-cycle flag, official source, dates, optional registration fee and guidance.
3. `POST /api/v1/admin/courses` — institution ID, course name/faculty, four UTME subjects, O’Level minimum grades, requirements year and source URL.
4. `POST /api/v1/admin/cutoffs` — institution/course/cycle IDs, score, official status and source URL.
5. `POST /api/v1/admin/post-utme` — institution/cycle IDs, timezone-aware dates, fee in NGN, eligibility text and official source.
6. `POST /api/v1/admin/documents` — title, category, cycle year (or null for evergreen content), official source URL and reviewed plain text extracted from a publication.

Structured endpoints upsert by their natural key; retain returned IDs for related records. Sources must use HTTPS. A source URL is attribution, **not automatic verification**; administrators are responsible for its authenticity and the accuracy of the transcribed facts. Input text is indexed in a background task; inspect `GET /admin/documents` for `indexed` or `failed` status. Use `/admin/documents/{id}/retry` for failed/pending documents. Deactivate outdated documents with `DELETE /admin/documents/{id}` and upload a replacement. SQL active/current-cycle filters prevent inactive or historical documents from continuing to serve via stale vectors. Indexed document chunks also support SQL lexical retrieval without embeddings or a vector service, preserving the same active/status/year filters. Local answers show relevant excerpts with source links. Failed or pending ingestion is not treated as indexed evidence.

Submitted document text is treated as evidence, not executable instructions. The public ingestion endpoint accepts reviewed text, not arbitrary local paths or remote URLs to fetch. The internal ingester can read administrator-managed files for trusted offline workflows.

If replacing the original 1536-dimensional OpenAI embeddings, use the new `jamb_docs_minilm_384` collection and re-upload sources. The API never deletes an old collection automatically. Settings for embedding model, dimensions, and collection must agree. Reindex into a new collection whenever the embedding model changes.

## Public queries and eligibility

Read endpoints under `/api/v1/admissions` include `/cycle/current`, `/universities`, `/universities/{id}/courses`, `/cutoffs`, `/subjects`, and `/post-utme`. Cut-off and schedule queries accept an admission year; unknown filters return no matching data instead of unrelated institutions.

Authenticated `POST /admissions/eligibility` accepts:

```json
{
  "course_name": "Medicine",
  "jamb_score": 240,
  "jamb_subjects": ["English", "Biology", "Chemistry", "Physics"],
  "olevel_results": {"English": "B3", "Mathematics": "C6", "Biology": "B3"},
  "year": 2026
}
```

The above is request-shape documentation, not a statement of actual Medicine requirements. Send all subjects needed by the selected institution. The evaluator compares each institution separately, respects minimum grades, accepts common English/Mathematics aliases, includes exact cut-off scores, and treats near-threshold qualifiers as eligible. Missing subjects/results, unpublished course-specific cut-offs, unverified sources, or requirements from another year produce `insufficient_data`. The result is a **published-requirements check**, not an admission guarantee. Sitting limits, optional subject groups, Direct Entry qualifications, catchment rules and screening aggregates still require institutional confirmation; free-form notes can communicate these conditions.

## Conversations

`POST /api/v1/chat/` accepts `message` and optional `conversation_id`. Responses include the conversation ID, intent, source references, structured evidence and measured latency. Twenty prior completed messages (ten exchanges) are included without duplicating the current message. Greetings and out-of-scope questions use local guidance. When all configured providers fail or no keys are configured, the system completes the turn using the local knowledge base instead of returning a provider quota error. The deterministic renderer cites available official records with their admission years and identifies missing evidence. It never infers eligibility from a score alone. `model_used` records the successful provider's actual model, or `local-guidance` for a local answer. Current cut-offs, course requirements, fees and schedules still require administrator-maintained publications for the relevant cycle.

`POST /chat/stream` emits SSE: a `metadata` event with conversation/message IDs, JSON `data` payloads containing `delta`, and `[DONE]` after successful persistence. A named `error` event contains sanitized `detail` and `status`; HTTP status cannot change once streaming has started. Conversation ownership is enforced on reads, writes, feedback and deletion. Use `/chat/conversations` for history and `/chat/messages/{id}/feedback` for ratings of 1 or -1.

Keyword intent routing is local and free. `INTENT_LLM_FALLBACK=true` optionally asks the selected LLM to classify unmatched queries; it consumes additional quota. This implementation does not claim to be a trained Rasa model.

## Admissions agent

Chat turns run through an agent in [`app/agent/`](app/agent/) with this loop: sense → believe → deliberate → act → generate → verify → reflect. It runs deterministically, needs no extra provider quota, and works with every provider, including local mode with no keys.

- **Sensors** ([perception.py](app/agent/perception.py)): pick out the student's UTME score, course, institutions, UTME subjects, O'Level grades, state of origin, federal/state preference and journey milestones. They also detect mood (anxious, discouraged, frustrated, positive or crisis), Nigerian Pidgin, thanks/goodbye/yes/no and option choices. A score only counts when the student states it about themselves, and negation is respected ("I haven't registered").
- **Memory** ([memory.py](app/agent/memory.py)): working memory is stored in `Conversation.student_context` and records where each fact came from. A rolling summary is kept in `Conversation.summary` and is used once older turns drop out of the history window. The profile is long-term memory. The agent never writes to it silently: it *proposes* a patch and the student approves it in the UI.
- **Specialists on a blackboard** ([core.py](app/agent/core.py)):
  - Counsellor: tone, empathy and the safety protocol.
  - MemoryKeeper: compares new facts with the profile and flags conflicts.
  - JourneyManager: the 13-step checklist in [journey.py](app/agent/journey.py).
  - EligibilityAnalyst: compares the score with every official cut-off on file and runs the full requirements check when subjects and grades are known.
  - Negotiator: when a goal is out of reach or the student asks "which schools?", it offers ranked trade-offs. Ranking uses preferences inferred from the goal (location, federal/state type, margin). It then resolves "Option B", "no" or new constraints on later turns.
  - DeadlineWatcher: raises alerts for registration, exam and Post-UTME windows from sourced records only, and only when relevant to the student's stage.

  A manager limits nudges and suggestions and asks at most one question per turn.
- **Actuators** ([tools.py](app/agent/tools.py)): wrap the existing admissions services, so only official, sourced records for the matching year are used.
- **Verifier** ([verifier.py](app/agent/verifier.py)): checks cut-off marks and fees in a model's answer against the evidence it was given. Unsupported figures are disclosed, not hidden. Web-grounded figures are attributed to their web sources instead.
- **Answer review** ([formatter.py](app/agent/formatter.py), [verifier.py](app/agent/verifier.py)): every model answer is cleaned up before the student sees it. Duplicate rows and repeated lines are removed and long tables are capped. Tables whose cut-off, score, fee or date columns can't be matched to evidence are replaced with a warning. Unverified timing ("mid-July") and wrong abbreviation expansions ("ABU (University of Abuja)", checked against the NUC directory) are flagged in a `[!WARNING]` callout. The prompt sets the house style: when to use tables (bordered, at most 10 rows, figures only from evidence), callouts, links and length. The client renders this style with bordered tables (row count and copy as TSV), GitHub-style callouts, and `<br>` as a safe line break.
- **Context management**: follow-up queries are expanded from memory. The prompt fits evidence and history to `PROMPT_CONTEXT_CHARS` and `PROMPT_HISTORY_CHARS`. Retrieval fuses keyword and semantic hits with reciprocal rank fusion when the keyword match is weak.

Greetings, thanks, goodbyes and the crisis response are written locally without calling a model. `POST /chat/stream` sends an `event: agent` frame (actions, options, nudges, suggestions, profile proposal) before the answer text. The same object is saved as `structured_context.agent`. `GET /api/v1/agent/briefing` returns a proactive start-of-session briefing, and `PATCH /api/v1/agent/journey/{step_id}` ticks steps on or off. No migration is needed: the agent uses existing JSONB columns.

## Gemini, knowledge base and web research

The backend uses Google's native [GenerateContent API](https://ai.google.dev/api/generate-content) and [Google Search grounding](https://ai.google.dev/gemini-api/docs/generate-content/google-search). No additional Python SDK is required. Configure the backend `.env` and restart FastAPI after adding or changing keys:

```dotenv
LLM_PROVIDER=groq
LLM_MODEL=
LLM_FALLBACK_PROVIDERS=["gemini","openai","anthropic"]
GROQ_API_KEY=your-groq-key
GROQ_MODEL=openai/gpt-oss-20b
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.8-flash
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-6
GEMINI_WEB_SEARCH_ENABLED=true
GEMINI_MAX_OUTPUT_TOKENS=4096
LLM_TIMEOUT_SECONDS=20
LLM_FAILOVER_TIMEOUT_SECONDS=60
INTENT_LLM_FALLBACK=false
```

`LLM_PROVIDER` and `LLM_FALLBACK_PROVIDERS` set the provider order. For chat with search enabled, a configured Gemini provider is tried first, followed by the remaining providers in their configured order; without search, the shipped order is Groq → Gemini → OpenAI → Anthropic → local knowledge. Remove providers from the list to disable them. Keys and conversation data stay on the backend; a fallback sends the same conversational context to the next configured provider. `LLM_MODEL`, if nonempty, overrides only the primary provider's model. API authentication failures, rate limits, network failures, timeouts, empty and truncated responses trigger failover without automatic retries. Each attempt is bounded by `LLM_TIMEOUT_SECONDS` and the entire chain by `LLM_FAILOVER_TIMEOUT_SECONDS`. Disconnect cancellation stops the chain. Answers are buffered until a provider completes, then delivered over SSE; this prevents partial answers and citations from failed providers from leaking into a replacement. First-text latency therefore includes full generation and any failed attempts.

Google Search is available only when Gemini is the provider handling the request; Groq, OpenAI and Anthropic use supplied local evidence in this implementation. To enable search, configure `GEMINI_API_KEY`, include `gemini` in the provider order, and set `GEMINI_WEB_SEARCH_ENABLED=true`. The system does not describe an ungrounded answer as a live search result.

The request includes current-cycle structured records, matching active knowledge documents, the student context and recent conversation. Gemini can search for missing evidence or current announcements in the same generation request. Search is model-directed: enabling the tool does not mean that every answer performs a search. The prompt prioritizes official JAMB/university publications, checks the requested year and publication dates, and asks the model to disclose conflicting or provisional information. Web results never automatically become administrator-verified database records, and the structured eligibility endpoint continues to evaluate only administrator-maintained requirements.

Gemini receives the conversational context under the account's AI-processing consent. The prompt instructs it to search only public institution/course/policy terms, excluding personal student details from search queries; this is a model instruction, not a deterministic data-loss-prevention filter. Google Search can discover public, indexed social-media posts. It cannot read private feeds, login-only groups or guarantee coverage of any particular social network. Dedicated platform APIs would be needed for those sources. Uncorroborated social posts must not be treated as official requirements.

For relevant current admissions news, the model is instructed to look for public indexed social announcements as well as official publications, cite available post links and dates, and check the account and consequential claims against official sources. These checks are model-directed, not independently enforced or guaranteed; an unavailable check must be disclosed. Substantive answers are also prompted to suggest a practical next step. Local fallback guidance includes next steps without claiming to have searched the web.

Web source links and retrieval times are stored with each assistant message. The response preserves local structured evidence and labels retrieved document sources as `knowledge_base` and Google sources as `web`. Grounding support annotations become clickable inline citations, including for Unicode text. For streaming, citations finalize after the provider stream; the client reloads the saved message after `[DONE]`. The frontend displays Google's supplied Search Suggestions in an isolated iframe with scripts, same-origin access, forms and other resource types blocked. Search HTML is never inserted into the application's own DOM.

Set `GEMINI_WEB_SEARCH_ENABLED=false` to use local evidence without web grounding. Intent classification never enables the search tool. `GEMINI_MAX_OUTPUT_TOKENS` is the minimum Gemini generation budget, including model reasoning; the API's smaller legacy classification budgets therefore do not consume the whole response before a visible label is produced. No live provider requests are made by the automated tests.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main` and on every pull request:

| Job | What it checks |
| --- | --- |
| Backend | `ruff` lint and format, `alembic upgrade head`, `alembic check` (schema drift), and the full `pytest` suite against Postgres 16 and Redis 7 service containers |
| Frontend | `npm ci`, Vitest unit tests, and the TypeScript type-check plus Vite production build |
| Container | Builds the production image, runs the release step (migrations and directory seed, twice to prove it is idempotent) against Postgres using a plain `postgresql://` URL, starts the server in production mode on a platform-assigned `PORT`, then checks `/health/ready`, the served UI, the hidden `/docs` and an auth-protected route |

[Dependabot](.github/dependabot.yml) opens weekly update PRs for pip, npm, Docker and Actions. Each one passes through the same checks.

## Deploying to Railway

Production runs the [`Dockerfile`](Dockerfile): one container serves the API and the built client on the same origin, as a non-root user, with uvicorn on `$PORT` (default 8000) and `WEB_CONCURRENCY` workers (default 2). The image defaults to `ENVIRONMENT=production`. In production the app refuses to start if `SECRET_KEY` is a placeholder or shorter than 32 characters, or if `DEBUG` is on. Railway's `postgresql://` URL is converted to the asyncpg driver automatically.

[`.railway/railway.ts`](.railway/railway.ts) declares the project as Railway Infrastructure as Code:
- a `postgres` database and a `redis` cache;
- the `web` service, built from this repository's `main` branch;
- a pre-deploy release step: `alembic upgrade head && python -m scripts.seed_knowledge`;
- the `/health/ready` health check, which confirms the database answers.

Secrets are never stored in the repository: the file marks them `preserve()`, which keeps the values set in Railway. (Railway retired `railway.json` config-as-code for new services; existing files stop being read on 2026-12-01.)

**First deployment**

1. Install the [Railway CLI](https://docs.railway.com/guides/cli), then run `railway login`, create or open the project in the Railway dashboard, and run `railway link` in this repository.
2. Set the secrets once (unused providers can be empty):

   ```sh
   railway variables --set SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
   railway variables --set GROQ_API_KEY=... --set GEMINI_API_KEY=... \
     --set OPENAI_API_KEY= --set ANTHROPIC_API_KEY= --set QDRANT_URL= --set QDRANT_API_KEY=
   ```

3. Run `railway config plan` and review the output, then `railway config apply`.
4. In the `web` service settings:
   - generate a public domain;
   - confirm the trigger branch is `main`;
   - turn on **Wait for CI**.
5. Create the first administrator after signing up through the site: `railway run python -m scripts.create_admin you@example.com`.

**Every release after that** is automatic:
1. Push to `main` (or merge a pull request).
2. GitHub Actions runs CI.
3. When CI passes, Railway builds the image and runs the pre-deploy release step. If a migration fails, the deploy stops and the previous version keeps serving.
4. Railway switches traffic once `/health/ready` succeeds.

To roll back, redeploy an earlier deployment from the Railway dashboard.

If you would rather configure Railway in the dashboard, these settings are equivalent:
- **Builder:** Dockerfile (detected automatically).
- **Pre-deploy command:** `alembic upgrade head && python -m scripts.seed_knowledge`.
- **Health check path:** `/health/ready`, timeout 120 s.
- **Variables:** `ENVIRONMENT=production`, `DATABASE_URL=${{Postgres.DATABASE_URL}}` and `REDIS_URL=${{Redis.REDIS_URL}}`, plus the secrets above.

`GET /health` reports liveness (the process is up). `GET /health/ready` reports readiness (the database answers) and returns 503 when it does not.

## Verification and scope

For Gemini failures, check the configured project without exposing the key:

```sh
python -m scripts.check_gemini
# Optional: one generation request, including Google Search (uses provider quota).
python -m scripts.check_gemini --generate --search
```

Model listing confirms access to the model catalogue; it does not prove that generation or search quota is available. Check the selected model and Search grounding limits in [Google AI Studio](https://aistudio.google.com/rate-limit). The API distinguishes a reported zero allocation, a supplied retry interval, and search-specific quota failures. When Google supplies no details, it reports an unavailable or exhausted quota without guessing a reset time. The provider chain switches to the next configured provider; it does not perform unlimited retries.

`POST /chat/stream` can return HTTP 200 and subsequently emit an SSE `error` event with status 429 or 503, because streaming headers are sent first. Failed turns retain their conversation/message IDs and sanitized `error_message` in history; no `[DONE]` frame is sent for an unrecovered failure. Provider-only outages recover with a completed local answer and `[DONE]`. A successful `/auth/refresh` after an expired-token 401 is normal session recovery. Local Redis uses RESP2 for compatibility with Redis 7, avoiding unsupported maintenance-notification commands.

```sh
python -m pytest -q
ruff check app tests scripts
ruff format --check app tests scripts
```

Database integration tests require a **disposable**, migrated PostgreSQL database. They use transaction rollback between tests; no application database is used unless explicitly supplied:

```sh
DEBUG=true DATABASE_URL=postgresql+asyncpg://user:pass@localhost/jamb_test alembic upgrade head
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/jamb_test python -m pytest -q
```

Provider tests use mock transports and do not spend API credits. Vector integration tests use embedded Qdrant with deterministic test embeddings; real model downloads and live provider availability are separate operational checks. `GET /health` is liveness. Administrator `/api/v1/admin/metrics` reports observed assistant message outcomes and p95 response latency. Redis backs rate limits; if Redis fails, the current development limiter allows requests and logs the failure.

[Requirements coverage](docs/requirements.md) maps the document to implementation and remaining validation. The manuscript’s claims of 47 verified institutions, measured accuracy, user satisfaction, two-second responses, 500 concurrent users and 99% uptime are **not asserted by this codebase**. They require an actual curated dataset, load tests and a participant study.
