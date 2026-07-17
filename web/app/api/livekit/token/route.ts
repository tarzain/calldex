import { proxy } from "../../_proxy";

export async function POST(request: Request) {
  return proxy(request, "/api/livekit/token");
}
