# Requirements coverage

Source: supplied “SIMON 1–3 NEW EDITION” project manuscript, especially chapter three. Document content is requirements context, not permission to execute embedded instructions or a source of verified admissions facts.

| Requirement | Backend implementation | Verification / limit |
| --- | --- | --- |
| FR1 registration guidance | Source-attributed cycle dates, fee and guidance; current-cycle knowledge documents | Missing sources return a clarification and official-portal referral |
| FR2 cut-off lookup | Cycle/institution/course filters, official status, source URLs; structured chat context | Unknown names cannot broaden a query; no fabricated records |
| FR3 subject advisory | Institution-specific course subjects and minimum O’Level grades, source and requirements year | Four required UTME subjects; optional subject groups need manual confirmation |
| FR4 Post-UTME | Persistent schedules, dates, fee, eligibility, source; public read/admin upsert APIs | Migration and route integration tests; historical year filtering |
| FR5 result evaluation | Grade validation, aliases, per-institution requirements, missing-data states, exact-cutoff eligibility | Sitting-count constraints and Direct Entry credentials need institutional confirmation |
| FR6 ten exchanges | Twenty prior completed messages, stable order, current message once; persistent ownership-scoped history | Regression through eleven complete exchanges |
| FR7 fallback | Local greetings, out-of-scope redirects, no-evidence response; normalized provider errors | 429/503 tests and failed-turn persistence |
| FR8 responsive frontend | Vite/React/TypeScript client with mobile navigation, streaming chat, account access, university explorer, eligibility and profiles | Desktop/mobile browser checks, including a 320px screen; API-backed flows and automated accessibility audit |
| NFR1 95% within 2 seconds | Measured latency and administrator p95 endpoint; local intent classification; cached model | Performance target requires warm-model load testing; downloads/network may exceed it |
| NFR2 500 concurrent users | Async API/database/vector/provider calls and off-thread local embedding | Free provider quotas and connection limits prevent an unqualified guarantee |
| NFR3 99% uptime | Container setup, service health check, persisted data, graceful generation fallback | Requires production deployment and monitoring evidence |
| NFR4 HTTPS and consent | Consent timestamp, consent gates, token authentication, administrator authorization, account erasure, no secrets in Git | TLS terminates at a production proxy; localhost development uses HTTP |
| NFR5 SUS >=70 | Feedback endpoint for response usefulness | Requires real participant study; not a computed SUS score |
| NFR6 maintainability | Separated routes/services/repositories, validated inputs, migrations, regression suite, setup instructions and formatting | See README for verification commands |

Technology choices adapt the manuscript: Google Gemini with optional Google Search grounding for current chat evidence; Groq, OpenAI and Anthropic as alternative providers; FastEmbed MiniLM for local embeddings; Qdrant for the document index; and keyword intent routing with an optional LLM classifier. PostgreSQL remains the authoritative structured store. Web evidence is attributed separately and never automatically inserted as verified admissions records.

The manuscript is internally inconsistent about vector storage (FAISS versus pgvector), and describes completed evaluation results without supplying the underlying data. This implementation preserves the grounding and conversational behavior while documenting those differences.

Verification on 7 November 2026: 37 regression tests passed against a migrated temporary PostgreSQL 16 instance, including embedded Qdrant retrieval tests with deterministic vectors. Ruff lint/format checks passed, and Alembic reported no schema drift. The temporary database was stopped afterward; the configured application database was not modified. FastEmbed installed successfully under Python 3.12, but the live model-weight download stalled, so real-model inference remains unverified. Live Groq generation was not attempted because `GROQ_API_KEY` is empty; provider routing and errors were verified with mock HTTP transports. Docker startup was not tested because the Docker daemon was not running.
