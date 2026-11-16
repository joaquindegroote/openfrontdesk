# openfrontdesk console

Next.js app with two pages:

- `/`: chat with the assistant as one of the clinic's seeded patients, with an "under the hood"
  panel showing each turn's tool calls, guardrail checks, latency and tokens.
- `/inbox`: the staff side. Conversations handed off by the assistant, with the reason and a
  summary; reply as the front desk and hand the conversation back.

```bash
npm install
npm run dev   # http://localhost:3000, expects the engine at http://localhost:8000
```

The browser never calls the engine. It calls the console's own server (`/api/engine/...`),
which forwards to the engine and adds the staff key, so the key stays out of the browser. Set
both in the server's environment, for example in `.env.local`:

- `ENGINE_URL`: where the engine is (default `http://localhost:8000`).
- `OFD_STAFF_KEY`: the engine's staff key. Without it, the inbox gets "staff endpoints are off".

Anyone who can open the console can act as staff, so it needs your own sign-in in front of it.
