# Security

## Reporting a vulnerability

Please report it privately: on this repository, open the **Security** tab and choose **Report a
vulnerability**. Don't open a public issue for it. I'll answer as soon as I can, and I'm glad to
credit you in the fix.

A report is most useful with the request or conversation that shows the problem, and what it
let you see or do that you shouldn't have.

## What is already known

These are in the README's [limitations](README.md#limitations), and don't need a report:

- The console has no sign-in of its own. Whoever can open it acts as the front desk.
- There are no rate limits on starting conversations or sending messages.
- Conversations are kept in the database with no retention period.

## If you deploy it

- Set your own `OFD_STAFF_KEY` and `OFD_INTEGRATION_KEY`. Docker Compose ships demo values that
  are public. Without the first, the staff endpoints are off; without the second, only web
  chats can be opened.
- Put the console behind your own sign-in, and a rate limit in front of the engine.
- `OFD_TRACE_CONTENT=false` keeps customer messages and tool data out of the traces.

[docs/architecture.md](docs/architecture.md#who-can-call-what) has who can call which endpoint
and what comes back to a caller without the staff key.
