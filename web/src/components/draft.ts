// The Create form draft in localStorage (PRD section 9). A convenience only: the library
// is the record, and storage may be unavailable, so every access is guarded.
//
// The draft is operation-neutral: a title and a request object. "Reuse settings" in the
// library and the result card writes one here; the Create form reads it.

export interface Draft {
  title: string;
  request: Record<string, unknown>;
}

const KEY = "studio.create.draft";
const listeners = new Set<(draft: Draft) => void>();

export function loadDraft(): Draft | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as unknown;
    if (typeof value !== "object" || value === null) return null;
    const { title, request } = value as { title?: unknown; request?: unknown };
    return {
      title: typeof title === "string" ? title : "",
      request: typeof request === "object" && request !== null ? (request as Record<string, unknown>) : {},
    };
  } catch {
    return null;
  }
}

export function saveDraft(draft: Draft): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(draft));
  } catch {
    // Storage full or blocked: the draft is only a convenience.
  }
}

/** Store a draft and tell a mounted Create form to load it. */
export function reuseDraft(draft: Draft): void {
  saveDraft(draft);
  for (const listener of listeners) listener(draft);
}

export function onDraftReused(listener: (draft: Draft) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
