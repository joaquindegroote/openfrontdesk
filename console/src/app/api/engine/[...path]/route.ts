// The browser talks to the engine through this route, so the keys stay on the server: the
// staff key for the inbox, and the integration key for the chat demo, which plays a WhatsApp
// channel. Anyone who can open the console acts as both: put it behind your own sign-in.

import type { NextRequest } from "next/server";

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, { params }: Context): Promise<Response> {
  const engine = process.env.ENGINE_URL ?? "http://localhost:8000";
  const { path } = await params;
  // The engine's API and nothing else.
  if (path[0] !== "v1" || path.some((part) => part === "." || part === "..")) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  const url = new URL(path.map(encodeURIComponent).join("/") + request.nextUrl.search, `${engine}/`);
  const headers = new Headers({ "Content-Type": "application/json" });
  const staffKey = process.env.OFD_STAFF_KEY;
  if (staffKey) headers.set("Authorization", `Bearer ${staffKey}`);
  const integrationKey = process.env.OFD_INTEGRATION_KEY;
  if (integrationKey) headers.set("X-Integration-Key", integrationKey);

  let response: Response;
  try {
    response = await fetch(url, {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
    });
  } catch {
    return Response.json(
      { detail: `Can't reach the engine at ${engine}. Is it running?` },
      { status: 502 },
    );
  }
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
  });
}

export async function GET(request: NextRequest, context: Context) {
  return forward(request, context);
}

export async function POST(request: NextRequest, context: Context) {
  return forward(request, context);
}
