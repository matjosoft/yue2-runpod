import type { ReactNode } from "react";
import { asUiError, type Generation } from "../../api/client";
import { useGeneration } from "../../api/queries";
import { ElapsedTimer } from "../../components/ElapsedTimer";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ReuseButton } from "../../components/ReuseButton";
import { StageList } from "../../components/StageList";
import { viewFor } from "../../components/operationViews";

function Spinner({ label }: { label: string }) {
  return (
    <p className="flex items-center gap-2">
      <span aria-hidden="true" className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-600" />
      {label}
    </p>
  );
}

/** ``live`` goes in the aria-live region; ``extra`` (timers, the player) does not. */
function generationView(generation: Generation, onRetry: (g: Generation) => void): { live: ReactNode; extra?: ReactNode } {
  switch (generation.status) {
    case "queued":
      return { live: <p>Waiting for the GPU</p>, extra: <ElapsedTimer since={generation.created_at} /> };
    case "running":
      return {
        live: (
          <>
            <p className="mb-2">Generating</p>
            <StageList stages={generation.stages} current={generation.stage} />
          </>
        ),
        extra: <ElapsedTimer since={generation.created_at} />,
      };
    case "downloading":
      return { live: <p>Fetching your song</p> };
    case "succeeded": {
      const { Result } = viewFor(generation.operation);
      return {
        live: <p className="font-semibold">{generation.title}</p>,
        extra: (
          <div className="space-y-3">
            <Result generation={generation} />
            <ReuseButton generation={generation} />
          </div>
        ),
      };
    }
    case "failed":
      return {
        live: (
          <ErrorMessage
            error={generation.error ?? { code: "internal", message: "The generation failed", retryable: false }}
          />
        ),
        extra: (
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              onRetry(generation);
            }}
          >
            Retry
          </button>
        ),
      };
    case "canceled":
      return { live: <p>This generation was canceled.</p> };
  }
}

interface Props {
  generationId: string | null;
  submitting: boolean;
  onRetry: (generation: Generation) => void;
}

export function ResultCard({ generationId, submitting, onRetry }: Props) {
  const query = useGeneration(generationId);
  let view: { live: ReactNode; extra?: ReactNode };
  if (submitting) view = { live: <Spinner label="Submitting…" /> };
  else if (!generationId) view = { live: <p className="text-slate-500">Your song will appear here</p> };
  else if (query.data) view = generationView(query.data, onRetry);
  else if (query.isError) view = { live: <ErrorMessage error={asUiError(query.error)} /> };
  else view = { live: <Spinner label="Loading…" /> };
  return (
    <section aria-label="Result" className="card space-y-3">
      <div aria-live="polite" data-testid="result-status">
        {view.live}
      </div>
      {view.extra}
      {query.data && query.isError && <ErrorMessage error={asUiError(query.error)} />}
    </section>
  );
}
