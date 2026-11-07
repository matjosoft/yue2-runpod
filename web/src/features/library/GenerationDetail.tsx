import { Link, useParams } from "react-router";
import { asUiError } from "../../api/client";
import { useGeneration } from "../../api/queries";
import { ArtifactList } from "../../components/artifacts";
import { ErrorMessage } from "../../components/ErrorMessage";
import { formatDateTime, formatSeconds } from "../../components/format";
import { ReuseButton } from "../../components/ReuseButton";
import { StatusChip } from "../../components/StatusChip";
import { viewFor } from "../../components/operationViews";

function show(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** /library/:id: the stored request (seed included), the artifacts with sizes, timings. */
export function GenerationDetail() {
  const { id } = useParams();
  const query = useGeneration(id);
  if (query.isPending) return <p>Loading…</p>;
  if (!query.data) {
    return (
      <div className="space-y-2">
        <ErrorMessage error={asUiError(query.error)} />
        <Link to="/library" className="underline">
          Back to the library
        </Link>
      </div>
    );
  }
  const g = query.data;
  const { Result } = viewFor(g.operation);
  const timings = Object.entries(g.timings);
  return (
    <article className="space-y-6">
      <Link to="/library" className="text-sm underline">
        ← Library
      </Link>
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{g.title}</h1>
        <StatusChip status={g.status} />
        <time className="text-sm text-slate-500" dateTime={g.created_at}>
          {formatDateTime(g.created_at)}
        </time>
      </header>
      {g.error && <ErrorMessage error={g.error} />}
      {g.status === "succeeded" && <Result generation={g} />}
      <ReuseButton generation={g} />
      <section>
        <h2 className="mb-2 text-lg font-semibold">Request</h2>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1" aria-label="Request">
          {Object.entries(g.request).map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="font-medium">{key}</dt>
              <dd>
                <pre className="font-mono text-sm whitespace-pre-wrap">{show(value)}</pre>
              </dd>
            </div>
          ))}
        </dl>
      </section>
      <section>
        <h2 className="mb-2 text-lg font-semibold">Files</h2>
        <ArtifactList artifacts={g.artifacts} />
      </section>
      <section>
        <h2 className="mb-2 text-lg font-semibold">Timings</h2>
        {timings.length === 0 ? (
          <p className="text-sm text-slate-500">None yet.</p>
        ) : (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm" aria-label="Timings">
            {timings.map(([key, seconds]) => (
              <div key={key} className="contents">
                <dt>{key}</dt>
                <dd>{formatSeconds(seconds)}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>
    </article>
  );
}
