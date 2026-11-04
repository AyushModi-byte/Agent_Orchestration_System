import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

/** Typed against the FastAPI OpenAPI schema: `npm run api:types` regenerates schema.d.ts. */
export const api = createClient<paths>({ baseUrl: import.meta.env.VITE_API_BASE ?? "/api" });

export type Schemas = components["schemas"];
export type TaskOut = Schemas["TaskOut"];
export type TaskSummary = Schemas["TaskSummary"];
export type TraceNode = Schemas["TraceNode"];
export type TraceTree = Schemas["TraceTree"];
export type SpanDetail = Schemas["SpanDetail"];
export type CostReport = Schemas["CostReport"];
export type MemoryDashboard = Schemas["MemoryDashboard"];
export type MemoryItem = Schemas["MemoryItem"];
export type ReplayDiff = Schemas["ReplayDiff"];

/** Thrown for any non-2xx so TanStack Query can show an error state with the server's message. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export function unwrap<T>(res: { data?: T; error?: unknown; response: Response }): T {
  if (res.error !== undefined || res.data === undefined) {
    const e = res.error as { detail?: unknown } | undefined;
    const detail = typeof e?.detail === "string" ? e.detail : JSON.stringify(e?.detail ?? e ?? "");
    throw new ApiError(detail || `HTTP ${res.response.status}`, res.response.status);
  }
  return res.data;
}
