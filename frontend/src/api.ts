// Client for POST /api/ask/stream. The event types mirror
// `service.astream_answer()`; adding one there means handling it here too.

import { BRAND } from "./brand";

/**
 * One retrieved passage. `page` numbers its page in the answer's citations ("[1]"); `source`
 * (the title) can be empty and `url` null, but not both.
 */
export type Source = {
  source: string;
  url: string | null;
  page: number;
  chunk_index: number;
  content: string;
};

/** Only pages on the client's site become links (AD-14). */
export const isSiteUrl = (url: string | null | undefined): url is string =>
  !!url && url.startsWith(BRAND.linkPrefix);

export type StreamEvent =
  | { type: "start"; thread_id: string; model?: string }
  | { type: "status"; stage: "searching" | "generating" }
  | { type: "token"; text: string }
  /** The guardrail blocked the answer streamed so far: show `text` instead (AD-18). */
  | { type: "replace"; text: string }
  | { type: "sources"; sources: Source[] }
  | { type: "done"; thread_id?: string; answer?: string; behaviour?: string }
  | { type: "error"; message: string };

/** A refusal from the API (400, 409, 422, 429 or 502) with its readable detail. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/**
 * Send one question and call `onEvent` for each server-sent event.
 * Pass the `thread_id` from the previous turn's `start` event to keep context.
 */
export async function streamAnswer(
  question: string,
  threadId: string | null,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const body = JSON.stringify(threadId ? { question, thread_id: threadId } : { question });
  const res = await fetch("/api/ask/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // On AWS, CloudFront signs the request to the API's Lambda function URL and cannot hash
      // a POST body itself, so the page sends the hash (AD-21). Ignored locally.
      "x-amz-content-sha256": await sha256Hex(body),
    },
    body,
    signal,
  });

  if (!res.ok || !res.body) {
    throw new ApiError(res.status, await errorDetail(res));
  }

  // EventSource cannot POST, so parse the SSE framing by hand: events are
  // separated by a blank line and each carries one `data:` line of JSON.
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    for (let end = buffer.indexOf("\n\n"); end !== -1; end = buffer.indexOf("\n\n")) {
      const frame = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      for (const line of frame.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }
}

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function errorDetail(res: Response): Promise<string> {
  const fallback = "Something went wrong. Please try again.";
  try {
    const body = await res.json();
    // FastAPI returns a string for our refusals and a list for validation errors.
    if (typeof body.detail === "string") return body.detail;
    if (res.status === 422) return "Please shorten your question and try again.";
  } catch {
    // Not JSON: fall through.
  }
  return fallback;
}
