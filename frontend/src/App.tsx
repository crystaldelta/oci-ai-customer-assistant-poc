import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, isSiteUrl, type Source, streamAnswer } from "./api";
import { BRAND } from "./brand";

type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
  error?: string;
};

const STAGE_LABEL = {
  searching: BRAND.searching,
  generating: BRAND.writing,
} as const;

let nextId = 1;

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [stage, setStage] = useState<keyof typeof STAGE_LABEL | null>(null);
  const [busy, setBusy] = useState(false);
  const [limitReached, setLimitReached] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll whenever a message or status changes
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, stage]);

  const updateLast = (patch: (m: Message) => Partial<Message>) =>
    setMessages((ms) => ms.map((m, i) => (i === ms.length - 1 ? { ...m, ...patch(m) } : m)));

  async function ask(question: string) {
    question = question.trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    setMessages((ms) => [
      ...ms,
      { id: nextId++, role: "user", text: question },
      { id: nextId++, role: "assistant", text: "" },
    ]);

    abort.current = new AbortController();
    try {
      await streamAnswer(
        question,
        threadId,
        (event) => {
          switch (event.type) {
            case "start":
              setThreadId(event.thread_id);
              break;
            case "status":
              setStage(event.stage);
              break;
            case "token":
              setStage(null);
              updateLast((m) => ({ text: m.text + event.text }));
              break;
            case "replace":
              updateLast(() => ({ text: event.text, sources: [] }));
              break;
            case "sources":
              updateLast((m) => ({ sources: [...(m.sources ?? []), ...event.sources] }));
              break;
            case "error":
              updateLast(() => ({ error: event.message }));
              break;
          }
        },
        abort.current.signal,
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      if (err instanceof ApiError && err.status === 409) setLimitReached(true);
      // The server no longer recognises this conversation: the next question starts a new one.
      if (err instanceof ApiError && err.status === 400) setThreadId(null);
      updateLast(() => ({ error: err instanceof Error ? err.message : String(err) }));
    } finally {
      setBusy(false);
      setStage(null);
    }
  }

  function newConversation() {
    abort.current?.abort();
    setMessages([]);
    setThreadId(null);
    setLimitReached(false);
    setInput("");
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    ask(input);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ask(input);
    }
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header__inner">
          <div className="brand">
            <span className="brand__name">{BRAND.name}</span>
            <span className="brand__product">{BRAND.product}</span>
          </div>
          <div className="header__actions">
            <button
              type="button"
              className="button button--ghost"
              onClick={newConversation}
              disabled={messages.length === 0}
            >
              New conversation
            </button>
          </div>
        </div>
      </header>

      <main className="conversation" aria-live="polite">
        {messages.length === 0 ? (
          <section className="welcome">
            <h1>{BRAND.welcome}</h1>
            <p>{BRAND.intro}</p>
            <div className="suggestions">
              {BRAND.suggestions.map((s) => (
                <button type="button" key={s} className="chip" onClick={() => ask(s)}>
                  {s}
                </button>
              ))}
            </div>
          </section>
        ) : (
          messages.map((m) => <Bubble key={m.id} message={m} />)
        )}
        {stage && <p className="stage">{STAGE_LABEL[stage]}</p>}
        <div ref={bottom} />
      </main>

      <footer className="composer">
        {limitReached ? (
          <div className="composer__limit">
            <span>This conversation has reached its limit.</span>
            <button type="button" className="button button--primary" onClick={newConversation}>
              Start a new conversation
            </button>
          </div>
        ) : (
          <form className="composer__form" onSubmit={onSubmit}>
            <textarea
              className="composer__input"
              rows={1}
              maxLength={2000}
              placeholder={BRAND.placeholder}
              aria-label="Your question"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
            />
            <button
              className="button button--primary"
              type="submit"
              disabled={busy || !input.trim()}
            >
              Send
            </button>
          </form>
        )}
        <p className="composer__note">{BRAND.footnote}</p>
      </footer>
    </div>
  );
}

// GFM, because models write tables, autolinks and strikethrough even when the
// prompt asks for plain answers. A wide table scrolls inside its bubble rather
// than widening the page on a phone.
const REMARK_PLUGINS = [remarkGfm];

/** One entry per page, in citation order: the first passage from each. */
function pagesOf(sources: Source[]): Source[] {
  const pages = new Map<number, Source>();
  for (const s of sources) if (!pages.has(s.page)) pages.set(s.page, s);
  return [...pages.values()].sort((a, b) => a.page - b.page);
}

// "[1]", "[1, 3]" or "【1】", but not a Markdown link's text ("[1](…)").
const CITATION = /[[【](\d+(?:\s*,\s*\d+)*)[\]】](?!\()/g;

/** The answer with each citation of a site page made a link to it; others stay as text. */
function linkCitations(text: string, pages: Source[]): string {
  const urls = new Map(pages.filter((p) => isSiteUrl(p.url)).map((p) => [p.page, p.url]));
  return text.replace(CITATION, (_match, numbers: string) =>
    numbers
      .split(",")
      .map((n) => {
        const url = urls.get(Number(n));
        return url ? `[${n.trim()}](${url})` : `[${n.trim()}]`;
      })
      .join(""),
  );
}

/**
 * Links only to the answer's own site pages: a link the model writes itself (a made-up or
 * altered URL) shows as plain text.
 */
function markdownComponents(pages: Source[]): Components {
  const allowed = new Set(pages.map((p) => p.url).filter((url) => isSiteUrl(url)));
  return {
    table: ({ node: _node, ...props }) => (
      <div className="bubble__table">
        <table {...props} />
      </div>
    ),
    a: ({ node: _node, href, children }) => {
      if (!href || !allowed.has(href)) return <>{children}</>;
      const cite = typeof children === "string" && /^\d+$/.test(children);
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className={cite ? "cite" : undefined}
        >
          {children}
        </a>
      );
    },
  };
}

function Bubble({ message }: Readonly<{ message: Message }>) {
  const { role, text, sources, error } = message;
  if (role === "user") return <div className="bubble bubble--user">{text}</div>;
  if (!text && !error) return null; // still waiting for the first token
  const pages = pagesOf(sources ?? []);
  return (
    <div className="bubble bubble--assistant">
      {text && (
        <Markdown remarkPlugins={REMARK_PLUGINS} components={markdownComponents(pages)}>
          {linkCitations(text, pages)}
        </Markdown>
      )}
      {error && <p className="bubble__error">{error}</p>}
      {pages.length > 0 && (
        <div className="sources">
          <span className="sources__label">Sources</span>
          <ol>
            {pages.map((p) => (
              <li key={p.page} value={p.page}>
                <SourceName source={p} />
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

/** The page's title, or its URL when it has none; a link when it is on the site. */
function SourceName({ source }: Readonly<{ source: Source }>) {
  const name = source.source || source.url || "Untitled page";
  if (!isSiteUrl(source.url)) return <>{name}</>;
  return (
    <a href={source.url} target="_blank" rel="noopener noreferrer">
      {name}
    </a>
  );
}
