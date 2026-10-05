// The client's name and the page's words, in one place. Colours, radii and the font are the
// `--brand-*` custom properties at the top of styles.css. `scripts/new_project.py` sets `name`
// and `linkPrefix`; change the rest by hand.

export const BRAND = {
  /** The client's name, shown in the header and the browser tab. */
  name: "Example Co",
  /** What the assistant is called under the name. */
  product: "Customer assistant",
  /**
   * Only source pages under this prefix become links: the client's own site. The service drops
   * any other URL (ALLOWED_LINK_PREFIX); this is the page's own check.
   */
  linkPrefix: "https://example.invalid/",
  welcome: "How can we help today?",
  intro: "Ask a question about your account, bills, payments or moving.",
  suggestions: [
    "How can I pay my bill?",
    "I'm moving house. What do I need to do?",
    "How do I make a complaint?",
  ],
  searching: "Searching our website…",
  writing: "Writing an answer…",
  placeholder: "Type your question",
  footnote: "Answers come from public website content and may not cover your situation.",
} as const;
