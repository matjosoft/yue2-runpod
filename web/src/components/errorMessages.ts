// Plain-language error messages (PRD section 9, U-6). The Record type makes tsc fail if
// an error code from the generated schema has no message.
import type { UiErrorCode, UiErrorInfo } from "../api/client";

export const ERROR_MESSAGES: Record<UiErrorCode, string> = {
  not_configured: "Add your worker URL and token in Settings.",
  worker_unreachable: "Can't reach the GPU worker. Is the pod running?",
  unauthorized: "The worker rejected the token.",
  not_ready: "The model is still loading. Try again in a minute.",
  queue_full: "The worker queue is full. Try again shortly.",
  validation_error: "Some fields are not valid.",
  oom: "The GPU ran out of memory. Try shorter lyrics, or retry.",
  engine_error: "The model failed to generate this song.",
  worker_restarted: "The worker restarted during generation. Retry.",
  download_failed:
    "The song was made but could not be downloaded. It stays on the worker for a limited time.",
  incompatible_worker: "This worker version does not match the app. Update the worker image.",
  // Not in the PRD table (docs/decisions.md, M3).
  not_found: "This could not be found. It may have been deleted.",
  conflict: "That isn't possible while the generation is in its current state.",
  internal: "Something unexpected went wrong. Try again.",
  server_unreachable: "Can't reach the app server. Is it running?",
};

const FALLBACK = "Something unexpected went wrong. Try again.";

export interface FieldError {
  loc: string;
  msg: string;
}

/** The ``details.fields`` list of a ``validation_error``: ``{loc: "request.style", msg}``. */
export function validationFields(info: Pick<UiErrorInfo, "code" | "details">): FieldError[] {
  if (info.code !== "validation_error") return [];
  const fields = info.details?.["fields"];
  if (!Array.isArray(fields)) return [];
  return fields.flatMap((field: unknown) => {
    if (typeof field !== "object" || field === null) return [];
    const { loc, msg } = field as { loc?: unknown; msg?: unknown };
    return typeof loc === "string" && typeof msg === "string" ? [{ loc, msg }] : [];
  });
}

/** Field messages keyed by the field name inside ``request``, e.g. ``style``. */
export function requestFieldErrors(info: UiErrorInfo): Record<string, string> {
  const out: Record<string, string> = {};
  for (const { loc, msg } of validationFields(info)) {
    const name = loc.startsWith("request.") ? loc.slice("request.".length) : loc;
    out[name] ??= msg;
  }
  return out;
}

export function plainMessage(info: Pick<UiErrorInfo, "code" | "details">): string {
  const fields = validationFields(info);
  if (fields.length > 0) {
    return fields.map(({ loc, msg }) => `${loc.replace(/^request\./, "")}: ${msg}`).join("\n");
  }
  return (ERROR_MESSAGES as Record<string, string | undefined>)[info.code] ?? FALLBACK;
}
