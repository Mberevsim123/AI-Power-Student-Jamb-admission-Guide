/**
 * Railway Infrastructure as Code for JAMB Guide (project "loving-nurturing").
 *
 *   railway login && railway link
 *   railway config plan     # review the changes
 *   railway config apply    # create/update the services
 *
 * Secrets are never written here: `preserve()` keeps the value already set in
 * Railway (`railway variable set KEY --stdin`, or the dashboard). Set them BEFORE
 * an apply — see README "Deploying to Railway".
 */
import { defineRailway, github, postgres, preserve, project, redis, service } from "railway/iac";

export default defineRailway(() => {
  const db = postgres("Postgres");
  const cache = redis("Redis");

  const web = service("AI-Power-Student-Jamb-admission-Guide", {
    // Pushes to main auto-deploy once the GitHub Actions checks pass ("Wait for CI").
    source: github("Mberevsim123/AI-Power-Student-Jamb-admission-Guide", {
      branch: "main",
      checkSuites: true,
    }),
    // The repository Dockerfile builds the client and API; its CMD starts uvicorn
    // on $PORT. Migrations and the NUC directory seed run once per release
    // (idempotent) before traffic moves to the new deployment.
    preDeploy: "python -m scripts.release", // one command: no shell needed
    healthcheck: "/health/ready",
    healthcheckTimeout: 120,
    env: {
      ENVIRONMENT: "production",
      DEBUG: "false",
      PORT: "8000", // the public domain targets this port
      WEB_CONCURRENCY: "2",
      DATABASE_URL: db.env.DATABASE_URL, // converted to asyncpg by the app
      REDIS_URL: cache.env.REDIS_URL,

      // Secrets: set in Railway, never in Git.
      SECRET_KEY: preserve(),
      GEMINI_API_KEY: preserve(),

      // Gemini answers (with Google Search grounding). Groq/OpenAI/Anthropic join the
      // failover chain automatically once their keys are added in Railway.
      LLM_PROVIDER: "gemini",
      LLM_FALLBACK_PROVIDERS: '["groq","openai","anthropic"]',
      GEMINI_MODEL: "gemini-3.8-flash",
      GROQ_MODEL: "openai/gpt-oss-20b",
      OPENAI_MODEL: "gpt-4.1-mini",
      ANTHROPIC_MODEL: "claude-sonnet-4-6",
      GEMINI_WEB_SEARCH_ENABLED: "true",
      GEMINI_MAX_OUTPUT_TOKENS: "4096",
      LLM_TIMEOUT_SECONDS: "20",
      LLM_FAILOVER_TIMEOUT_SECONDS: "60",
      EMBEDDING_PROVIDER: "local",
      QDRANT_COLLECTION: "jamb_docs_minilm_384",
    },
  });

  return project("loving-nurturing", { resources: [db, cache, web] });
});
