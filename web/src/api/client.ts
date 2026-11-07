// The local server API client, typed from the generated schema.d.ts (make contracts).
// The UI talks only to the local server (PRD section 5), never to the worker.
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type Generation = Schemas["Generation"];
export type GenerationArtifact = Schemas["GenerationArtifact"];
export type GenerationCreate = Schemas["GenerationCreate"];
export type GenerationList = Schemas["GenerationList"];
export type GenerationStatus = Schemas["GenerationStatus"];
export type SettingsView = Schemas["SettingsView"];
export type SettingsUpdate = Schemas["SettingsUpdate"];
export type WorkerCheck = Schemas["WorkerCheck"];
export type WorkerCheckState = Schemas["WorkerCheckState"];
export type ServerHealth = Schemas["ServerHealth"];
export type Capabilities = Schemas["Capabilities"];
export type ServerErrorInfo = Schemas["ServerErrorInfo"];
export type ErrorCode = Schemas["ErrorCode"];
export type ClientErrorCode = Schemas["ClientErrorCode"];

/** Error codes the UI can show: the server's codes, plus one for when the local server
 * itself cannot be reached (it is not part of the API contract). */
export type UiErrorCode = ErrorCode | ClientErrorCode | "server_unreachable";

export interface UiErrorInfo {
  code: UiErrorCode;
  message: string;
  retryable: boolean;
  details?: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly info: UiErrorInfo;
  /** HTTP status; 0 when no response arrived. */
  readonly status: number;

  constructor(info: UiErrorInfo, status: number) {
    super(info.message);
    this.name = "ApiError";
    this.info = info;
    this.status = status;
  }
}

export const TERMINAL_STATUSES: ReadonlySet<GenerationStatus> = new Set([
  "succeeded",
  "failed",
  "canceled",
]);

export function isTerminal(status: GenerationStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

export const api = createClient<paths>({
  baseUrl: typeof window === "undefined" ? "" : window.location.origin,
  // Look fetch up on every call, so tests can stub it after this module loads.
  fetch: (request: Request) => globalThis.fetch(request),
});

function isEnvelope(value: unknown): value is { error: ServerErrorInfo } {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { code?: unknown }).code === "string" &&
    typeof (error as { message?: unknown }).message === "string"
  );
}

export function toApiError(body: unknown, status: number): ApiError {
  if (isEnvelope(body)) {
    return new ApiError({ ...body.error, details: body.error.details ?? {} }, status);
  }
  return new ApiError(
    {
      code: "internal",
      message: `The app server answered HTTP ${status.toString()}`,
      retryable: false,
      details: {},
    },
    status,
  );
}

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** Await an openapi-fetch call: the data on 2xx, otherwise an ApiError. */
export async function call<T>(request: Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await request;
  } catch {
    throw new ApiError(
      {
        code: "server_unreachable",
        message: "No response from the app server",
        retryable: true,
        details: {},
      },
      0,
    );
  }
  if (!result.response.ok) {
    throw toApiError(result.error, result.response.status);
  }
  return result.data as T;
}

export function asUiError(error: unknown): UiErrorInfo {
  if (error instanceof ApiError) return error.info;
  return {
    code: "internal",
    message: error instanceof Error ? error.message : String(error),
    retryable: false,
    details: {},
  };
}
