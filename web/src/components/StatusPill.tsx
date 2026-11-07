import { Link } from "react-router";
import type { WorkerCheckState } from "../api/client";
import { useHealth } from "../api/queries";

type Tone = "grey" | "red" | "amber" | "green";

// U-5: grey = not configured, red = unreachable or unauthorized, amber = warming up,
// green = ready. The M2 states `incompatible` and `error` are red too.
export const PILL: Record<WorkerCheckState, { tone: Tone; label: string }> = {
  not_configured: { tone: "grey", label: "Not configured" },
  unreachable: { tone: "red", label: "Unreachable" },
  unauthorized: { tone: "red", label: "Unauthorized" },
  incompatible: { tone: "red", label: "Incompatible" },
  error: { tone: "red", label: "Model error" },
  warming_up: { tone: "amber", label: "Warming up" },
  ready: { tone: "green", label: "Ready" },
};

const DOT: Record<Tone, string> = {
  grey: "bg-slate-400",
  red: "bg-red-600",
  amber: "bg-amber-500",
  green: "bg-green-600",
};

export function StatusPill() {
  const health = useHealth();
  // A failed poll wins over the last good answer, which may be stale by now.
  const pill = health.isError
    ? { tone: "red" as const, label: "App server unreachable" }
    : health.data
      ? PILL[health.data.worker.state]
      : { tone: "grey" as const, label: "Checking" };
  return (
    <Link
      to="/settings"
      data-testid="status-pill"
      data-tone={pill.tone}
      aria-label={`Worker status: ${pill.label}. Open settings`}
      className="flex items-center gap-2 rounded-full border border-slate-300 px-3 py-1 text-sm dark:border-slate-600"
    >
      <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${DOT[pill.tone]}`} />
      <span aria-live="polite">{pill.label}</span>
    </Link>
  );
}
