import { proxy } from "../../_proxy";

export async function GET(request: Request, context: { params: Promise<{ threadId: string }> }) {
  const { threadId } = await context.params;
  return proxy(request, `/api/threads/${encodeURIComponent(threadId)}`);
}

export async function PATCH(request: Request, context: { params: Promise<{ threadId: string }> }) {
  const { threadId } = await context.params;
  return proxy(request, `/api/threads/${encodeURIComponent(threadId)}`);
}
