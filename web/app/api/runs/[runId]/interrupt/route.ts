import { proxy } from "../../../_proxy";

export async function POST(request: Request, context: { params: Promise<{ runId: string }> }) {
  const { runId } = await context.params;
  return proxy(request, `/api/runs/${encodeURIComponent(runId)}/interrupt`);
}
