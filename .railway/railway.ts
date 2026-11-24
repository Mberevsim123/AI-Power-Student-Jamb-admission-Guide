/**
 * Railway Infrastructure as Code for JAMB Guide.
 *
 *   railway login && railway link
 *   railway config plan     # review the changes
 *   railway config apply    # create/update the services
 *
 * Secrets are never written here: `preserve()` keeps the value already set in
 * Railway (dashboard → Variables, or `railway variables --set KEY=value`).
 * Set them BEFORE the first apply — see README "Deploying to Railway".
 */
import { defineRailway, github, postgres, preserve, project, redis, service } from "railway/iac";

export default defineRailway(() => {
  const db = postgres("postgres");
  const cache = redis("redis");

  const web = service("web", {
    // Pushes to main auto-deploy. Enable "Wait for CI" in the service settings so a
    // deploy only starts after the GitHub Actions checks pass.
    source: github("Mberevsim123/AI-Power-Student-Jamb-admission-Guide", { branch: "main" }),
    // The repository Dockerfile builds the client and API; its CMD starts uvicorn
    // on $PORT. Schema migrations and the NUC directory seed run once per release
    // (idempotent) before traffic moves to the new deployment.
    preDeploy: "alembic upgrade head && python -m scripts.seed_knowledge",
    healthcheck: "/health/ready",
    healthcheckTimeout: 120,
    replicas: 1,
    env: {
      ENVIRONMENT: "production",
      DEBUG: "false",
      WEB_CONCURRENCY: "2",
      DATABASE_URL: db.env.DATABASE_URL, // converted to asyncpg by the app
      REDIS_URL: cache.env.REDIS_URL,

      // Secrets and optional integrations: set in Railway, never in Git.
      SECRET_KEY: preserve(),
      GROQ_API_KEY: preserve(),
      GEMINI_API_KEY: preserve(),
      OPENAI_API_KEY: preserve(),
      ANTHROPIC_API_KEY: preserve(),
      QDRANT_URL: preserve(),
      QDRANT_API_KEY: preserve(),

      // Non-secret model routing (same defaults as .env.example).
      LLM_PROVIDER: "groq",
      LLM_FALLBACK_PROVIDERS: '["gemini","openai","anthropic"]',
      GROQ_MODEL: "openai/gpt-oss-20b",
      GEMINI_MODEL: "gemini-3.8-flash",
      OPENAI_MODEL: "gpt-4.1-mini",
      ANTHROPIC_MODEL: "claude-sonnet-4-6",
      GEMINI_WEB_SEARCH_ENABLED: "true",
      EMBEDDING_PROVIDER: "local",
      QDRANT_COLLECTION: "jamb_docs_minilm_384",
    },
  });

  return project("ai-power-student-jamb-admission-guide", { resources: [db, cache, web] });
});
