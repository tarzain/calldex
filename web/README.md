# Calldex dashboard

React 19 + TypeScript dashboard built with Tailwind CSS, shadcn/ui, AI Elements, and LiveKit Components. It is launched from the repository root with:

```bash
uv run calldex-dashboard
```

For frontend-only development, keep the Calldex API running on `127.0.0.1:8765`, then run `npm run dev` in this directory.

The API route handlers proxy to the loopback FastAPI server, keeping browser requests and LiveKit token acquisition same-origin.
