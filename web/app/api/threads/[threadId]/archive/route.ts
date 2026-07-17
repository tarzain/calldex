import { proxy } from "../../../_proxy";

export async function POST(request: Request, context: { params: Promise<{ threadId: string }> }) {
  const { threadId } = await context.params;
  return proxy(request, `/api/threads/${encodeURIComponent(threadId)}/archive`);
}
