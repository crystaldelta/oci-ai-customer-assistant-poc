// CloudFront Function: the shared demo password (AD-21).
//
// Viewer request, runtime cloudfront-js-2.0, on every cache behaviour (the page and /api/*).
// The browser asks for a username and password once and then sends them with every request,
// including the page's calls to /api, so nothing else in the app knows about the password.
//
// The credentials live in a CloudFront KeyValueStore associated with this function, not here:
//   key   basic-auth
//   value base64 of "<username>:<password>", e.g. from: printf 'demo:<password>' | base64
// Changing that value changes the password, with no redeploy. Replace KVS_ID below with the
// store's ID when deploying.
import cf from "cloudfront";

const kvs = cf.kvs("KVS_ID");

const DENIED = {
  statusCode: 401,
  statusDescription: "Unauthorized",
  headers: { "www-authenticate": { value: 'Basic realm="ai-starter demo", charset="UTF-8"' } },
};

async function handler(event) {
  const header = event.request.headers.authorization;
  if (!header) return DENIED;
  try {
    const expected = await kvs.get("basic-auth");
    return header.value === `Basic ${expected}` ? event.request : DENIED;
  } catch (e) {
    return DENIED; // no store or no key: closed, never open
  }
}
