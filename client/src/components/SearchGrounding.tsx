/** Provider search suggestions are isolated from the app and its account session. */
export function SearchGrounding({ value }: { value: unknown }) {
  if (!value || typeof value !== "object") return null;
  const data = value as Record<string, unknown>;
  if (data.provider !== "google_search") return null;
  const html = data.search_suggestions_html;
  if (typeof html !== "string" || !html.trim()) return null;
  const policy =
    "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; base-uri 'none'; form-action 'none'";
  return (
    <div className="search-grounding">
      <iframe
        title="Google Search suggestions"
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        srcDoc={`<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${policy}"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body>${html}</body></html>`}
      />
    </div>
  );
}
