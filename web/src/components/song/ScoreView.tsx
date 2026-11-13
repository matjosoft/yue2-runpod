import { useLocation, useNavigate } from "react-router";
import { asUiError, type GenerationArtifact } from "../../api/client";
import { useArtifactText } from "../../api/queries";
import { reuseDraft } from "../draft";
import { ErrorMessage } from "../ErrorMessage";
import type { OperationViewProps } from "../operationViews";
import { CREATE_PATH } from "../ReuseButton";

/** The score the model sang, chosen by role (never by file name). */
export function scoreArtifact(artifacts: GenerationArtifact[]): GenerationArtifact | undefined {
  return artifacts.find((a) => a.role === "score");
}

/** The ABC score of a finished song, and "Edit score": the Create form, filled with this
 * song's settings (seed included) and its score, ready to change and create again. */
export function ScoreView({ generation }: OperationViewProps) {
  const artifact = scoreArtifact(generation.artifacts);
  const query = useArtifactText(artifact?.url);
  const navigate = useNavigate();
  const location = useLocation();
  if (!artifact) return null;

  const edit = (abc: string) => {
    // A score needs planning; a song planned with "melody" keeps that, anything else is full.
    const cot = generation.request["cot"] === "melody" ? "melody" : "full";
    reuseDraft({ title: "", request: { ...generation.request, abc, cot } });
    if (location.pathname !== CREATE_PATH) void navigate(CREATE_PATH);
  };

  return (
    <section aria-label="Score" className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">Score (ABC)</h3>
        <button
          type="button"
          className="btn-secondary"
          disabled={query.data === undefined}
          onClick={() => {
            if (query.data !== undefined) edit(query.data);
          }}
        >
          Edit score
        </button>
      </div>
      {query.data !== undefined ? (
        <pre
          data-testid="score-text"
          className="max-h-72 overflow-auto rounded-md bg-slate-50 p-3 font-mono text-sm whitespace-pre dark:bg-slate-900"
        >
          {query.data}
        </pre>
      ) : query.isError ? (
        <ErrorMessage error={asUiError(query.error)} />
      ) : (
        <p className="text-sm text-slate-500">Loading score…</p>
      )}
    </section>
  );
}
