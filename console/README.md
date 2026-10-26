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

Set `NEXT_PUBLIC_ENGINE_URL` to point at another engine.
