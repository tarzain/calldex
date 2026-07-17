const API_ORIGIN = process.env.CALLDEX_API_ORIGIN || "http://127.0.0.1:8765";

export async function proxy(request: Request, path: string) {
  const upstream = new URL(path, API_ORIGIN);
  const incoming = new URL(request.url);
  upstream.search = incoming.search;
  const response = await fetch(upstream, {
    method: request.method,
    headers: request.headers.get("content-type") ? { "content-type": request.headers.get("content-type")! } : undefined,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.text(),
    cache: "no-store",
  });
  return new Response(response.body, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") || "application/json",
      ...(response.headers.get("cache-control") ? { "cache-control": response.headers.get("cache-control")! } : {}),
    },
  });
}
