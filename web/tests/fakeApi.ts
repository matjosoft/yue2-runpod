// A stub for fetch that routes by method and path, plus builders for API bodies. Bodies
// are typed from the generated schema, so tests break when the contract changes.
import { vi } from "vitest";
import type {
  Capabilities,
  Generation,
  ServerErrorInfo,
  ServerHealth,
  WorkerCheckState,
} from "../src/api/client";

export interface FakeRequest {
  method: string;
  path: string;
  url: URL;
  body: unknown;
}

export interface FakeResponse {
  status?: number;
  body?: unknown;
}

type Handler = (req: FakeRequest) => FakeResponse | Promise<FakeResponse>;

export interface FakeApi {
  calls: FakeRequest[];
  /** Replace or add a route; later routes win over earlier ones. */
  on(method: string, path: string | RegExp, handler: Handler | FakeResponse): void;
  callsTo(method: string, path: string | RegExp): FakeRequest[];
}

function matches(pattern: string | RegExp, path: string): boolean {
  return typeof pattern === "string" ? pattern === path : pattern.test(path);
}

export function installFakeApi(): FakeApi {
  const routes: { method: string; path: string | RegExp; handler: Handler }[] = [];
  const calls: FakeRequest[] = [];

  const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = new URL(request.url);
    const text = await request.text();
    const req: FakeRequest = {
      method: request.method,
      path: url.pathname,
      url,
      body: text ? (JSON.parse(text) as unknown) : undefined,
    };
    calls.push(req);
    const route = [...routes]
      .reverse()
      .find((r) => r.method === req.method && matches(r.path, req.path));
    const res: FakeResponse = route
      ? await route.handler(req)
      : { status: 404, body: envelope("not_found", `no fake route for ${req.method} ${req.path}`) };
    const status = res.status ?? 200;
    if (status === 204) return new Response(null, { status });
    return new Response(JSON.stringify(res.body ?? null), {
      status,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchStub);

  return {
    calls,
    on(method, path, handler) {
      routes.push({ method, path, handler: typeof handler === "function" ? handler : () => handler });
    },
    callsTo(method, path) {
      return calls.filter((c) => c.method === method && matches(path, c.path));
    },
  };
}

export function envelope(
  code: ServerErrorInfo["code"],
  message: string,
  details: Record<string, unknown> = {},
): { error: ServerErrorInfo } {
  return { error: { code, message, retryable: false, details } };
}

export const SONG_STAGES = ["plan", "semantic", "synthesis", "decode", "save"];

export function makeGeneration(overrides: Partial<Generation> = {}): Generation {
  const id = overrides.id ?? "gen_01TEST";
  return {
    id,
    title: "Untitled 2026-11-07 10:00",
    operation: "song.generate",
    request: { style: "English, jazz-funk", lyrics: "[Verse]\nla la", seed: 12345, cot: "full" },
    status: "queued",
    stage: null,
    stages: SONG_STAGES,
    error: null,
    worker_job_id: "job_01TEST",
    timings: {},
    artifacts: [],
    created_at: "2026-11-07T10:00:00Z",
    updated_at: "2026-11-07T10:00:00Z",
    ...overrides,
  };
}

export function succeededGeneration(overrides: Partial<Generation> = {}): Generation {
  const id = overrides.id ?? "gen_01TEST";
  const url = (name: string) => `/api/generations/${id}/artifacts/${name}`;
  return makeGeneration({
    status: "succeeded",
    stage: "save",
    timings: { queue_s: 0.1, plan_s: 1.2, total_s: 5.5 },
    artifacts: [
      { name: "audio.flac", role: "primary_audio", media_type: "audio/flac", size_bytes: 31457280, url: url("audio.flac") },
      { name: "score.abc", role: "score", media_type: "text/plain", size_bytes: 2048, url: url("score.abc") },
      { name: "result.json", role: "result", media_type: "application/json", size_bytes: 512, url: url("result.json") },
    ],
    ...overrides,
  });
}

export function health(state: WorkerCheckState): ServerHealth {
  return {
    ok: true,
    worker: {
      state,
      error: null,
      worker:
        state === "ready" || state === "warming_up"
          ? { api_version: "1.0", worker_version: "0.1.0", model_state: state === "ready" ? "ready" : "loading" }
          : null,
    },
  };
}

export function capabilities(operations: string[] = ["song.generate"]): Capabilities {
  return {
    api_version: "1.0",
    worker_version: "0.1.0",
    engine: { kind: "fake", package_version: "0", models: {} },
    operations: operations.map((name) => ({ name, stages: SONG_STAGES, params_schema: {} })),
  };
}

/** The routes almost every page needs: a ready worker that offers song.generate. */
export function installDefaultApi(): FakeApi {
  const fake = installFakeApi();
  fake.on("GET", "/api/health", { body: health("ready") });
  fake.on("GET", "/api/capabilities", { body: capabilities() });
  fake.on("GET", "/api/settings", { body: { worker_url: "http://127.0.0.1:8000", token_set: true } });
  fake.on("GET", "/api/generations", { body: { items: [], next_cursor: null } });
  return fake;
}
