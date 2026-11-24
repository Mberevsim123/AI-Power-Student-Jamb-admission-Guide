import { useRef, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle,
  ArrowUpRight,
  Check,
  Copy,
  Info,
  Lightbulb,
  OctagonAlert,
  ShieldAlert,
} from "lucide-react";
import { safeUrl } from "../lib/api";

type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

const CALLOUTS = {
  note: { label: "Note", icon: Info },
  tip: { label: "Tip", icon: Lightbulb },
  important: { label: "Important", icon: ShieldAlert },
  warning: { label: "Warning", icon: AlertTriangle },
  caution: { label: "Caution", icon: OctagonAlert },
} as const;
type CalloutKind = keyof typeof CALLOUTS;
const BREAK = /<br\s*\/?>/i;

/**
 * Dependency-free rehype plugin:
 * - "> [!TIP]" style blockquotes become typed callouts (GitHub alert syntax);
 * - a literal <br> (raw HTML stays disabled) becomes a real line break, so
 *   multi-line table cells written by a model render correctly and safely.
 */
function rehypeHouseStyle() {
  const splitBreaks = (nodes: HastNode[]): HastNode[] =>
    nodes.flatMap((node) => {
      if (node.type === "raw" && BREAK.test(node.value || "") && !(node.value || "").replace(/<br\s*\/?>/gi, "").trim())
        return [{ type: "element", tagName: "br", properties: {}, children: [] }];
      if (node.type === "text" && BREAK.test(node.value || ""))
        return (node.value || "")
          .split(/<br\s*\/?>/i)
          .flatMap((part, i): HastNode[] => [
            ...(i ? [{ type: "element", tagName: "br", properties: {}, children: [] }] : []),
            ...(part ? [{ type: "text", value: part }] : []),
          ]);
      return [node];
    });
  const visit = (node: HastNode) => {
    if (node.children) {
      node.children = splitBreaks(node.children);
      node.children.forEach(visit);
    }
    if (node.tagName !== "blockquote") return;
    const first = node.children?.find((c) => c.type === "element");
    const text = first?.children?.[0];
    const match = text?.type === "text" && /^\s*\[!(note|tip|important|warning|caution)\]\s*/i.exec(text.value || "");
    if (!first || !text || !match) return;
    text.value = (text.value || "").slice(match[0].length);
    // Drop the break left behind when the marker sat on its own line.
    if (!text.value.trim()) {
      first.children!.shift();
      if (first.children?.[0]?.tagName === "br") first.children.shift();
    }
    node.properties = { ...node.properties, dataCallout: match[1].toLowerCase() };
  };
  return (tree: HastNode) => visit(tree);
}

function textOf(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (typeof node === "object" && "props" in node)
    return textOf((node as { props: { children?: ReactNode } }).props.children);
  return "";
}

function DataTable({ children }: { children?: ReactNode }) {
  const ref = useRef<HTMLTableElement>(null);
  const [copied, setCopied] = useState(false);
  const [rows, setRows] = useState<number | null>(null);
  async function copy() {
    const table = ref.current;
    if (!table) return;
    // Tab-separated values paste cleanly into Excel, Sheets and Word.
    const tsv = Array.from(table.rows)
      .map((row) =>
        Array.from(row.cells)
          .map((cell) => (cell.innerText || "").replace(/\s*\n\s*/g, " ").trim())
          .join("\t"),
      )
      .join("\n");
    try {
      await navigator.clipboard.writeText(tsv);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* Clipboard may be blocked; the table remains selectable. */
    }
  }
  return (
    <figure className="md-table">
      <div className="md-table-scroll" tabIndex={0} role="region" aria-label="Table">
        <table
          ref={(node) => {
            ref.current = node;
            if (node && rows === null) setRows(node.tBodies[0]?.rows.length ?? 0);
          }}
        >
          {children}
        </table>
      </div>
      <figcaption>
        <span>
          {rows ?? 0} row{rows === 1 ? "" : "s"}
        </span>
        <button type="button" onClick={copy} aria-label="Copy table">
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </figcaption>
    </figure>
  );
}

const components: Components = {
  a: ({ href, children }) => {
    const url = safeUrl(href);
    if (!url) return <span>{children}</span>;
    return (
      <a href={url} target="_blank" rel="noopener noreferrer" className="md-link">
        {children}
        <ArrowUpRight size={12} aria-hidden="true" />
        <span className="sr-only"> (opens in a new tab)</span>
      </a>
    );
  },
  table: ({ children }) => <DataTable>{children}</DataTable>,
  th: ({ children, style }) => (
    <th scope="col" style={style}>
      {children}
    </th>
  ),
  td: ({ children, style }) => {
    // Right-align numeric cells even when the model omits ---: alignment.
    const numeric = /^[\s₦$+\-–]*[\d.,]+\s*%?\s*$/.test(textOf(children));
    return (
      <td style={style} className={numeric ? "num" : undefined}>
        {children}
      </td>
    );
  },
  blockquote: ({ node, children }) => {
    const kind = node?.properties?.dataCallout as CalloutKind | undefined;
    if (!kind || !(kind in CALLOUTS)) return <blockquote>{children}</blockquote>;
    const { label, icon: Icon } = CALLOUTS[kind];
    return (
      <aside className={`md-callout ${kind}`} role="note" aria-label={label}>
        <div className="md-callout-title">
          <Icon size={16} aria-hidden="true" />
          {label}
        </div>
        <div className="md-callout-body">{children}</div>
      </aside>
    );
  },
  input: ({ checked }) => (
    <input type="checkbox" checked={Boolean(checked)} readOnly disabled className="md-task" />
  ),
};

/** Raw HTML stays disabled; publication URLs are restricted to HTTP(S). */
export default function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHouseStyle]}
      components={components}
    >
      {children}
    </ReactMarkdown>
  );
}
