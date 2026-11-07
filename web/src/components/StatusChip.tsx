import type { GenerationStatus } from "../api/client";

const TONE: Record<GenerationStatus, string> = {
  queued: "bg-slate-200 text-slate-800 dark:bg-slate-700 dark:text-slate-100",
  running: "bg-blue-100 text-blue-900 dark:bg-blue-900 dark:text-blue-100",
  downloading: "bg-blue-100 text-blue-900 dark:bg-blue-900 dark:text-blue-100",
  succeeded: "bg-green-100 text-green-900 dark:bg-green-900 dark:text-green-100",
  failed: "bg-red-100 text-red-900 dark:bg-red-900 dark:text-red-100",
  canceled: "bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-100",
};

export function StatusChip({ status }: { status: GenerationStatus }) {
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${TONE[status]}`}>{status}</span>;
}
