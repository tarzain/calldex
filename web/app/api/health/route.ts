import { proxy } from "../_proxy";

export async function GET(request: Request) {
  return proxy(request, "/api/health");
}
