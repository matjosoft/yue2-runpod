import { asUiError, type Generation } from "../api/client";
import { useCancelGeneration } from "../api/queries";
import { ErrorMessage } from "./ErrorMessage";

/** Statuses the server can cancel (S-9). ``downloading`` is not one: the worker job has
 * already finished. */
export function isCancelable(generation: Generation): boolean {
  return generation.status === "queued" || generation.status === "running";
}

/** Cancel a queued or running generation. A running one stops at the worker's next
 * stage boundary, so after a successful request the button reads "Canceling…" until
 * the polled status turns ``canceled``. Not part of the PRD §9 result-card table
 * (docs/decisions.md, M5). Renders nothing for other statuses. */
export function CancelButton({ generation, label }: { generation: Generation; label?: string }) {
  const cancel = useCancelGeneration();
  if (!isCancelable(generation)) return null;
  const requested = cancel.isPending || cancel.isSuccess;
  return (
    <div className="space-y-2">
      <button
        type="button"
        className="btn-secondary"
        disabled={requested}
        {...(label ? { "aria-label": label } : {})}
        onClick={() => {
          cancel.mutate(generation.id);
        }}
      >
        {requested ? "Canceling…" : "Cancel generation"}
      </button>
      {cancel.error && <ErrorMessage error={asUiError(cancel.error)} />}
    </div>
  );
}
