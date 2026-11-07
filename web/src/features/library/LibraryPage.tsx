import { useState } from "react";
import { Link } from "react-router";
import { asUiError, isTerminal, type Generation } from "../../api/client";
import { useDeleteGeneration, useGenerationList } from "../../api/queries";
import { primaryAudio } from "../../components/artifacts";
import { CancelButton } from "../../components/CancelButton";
import { ErrorMessage } from "../../components/ErrorMessage";
import { formatDateTime } from "../../components/format";
import { ReuseButton } from "../../components/ReuseButton";
import { StatusChip } from "../../components/StatusChip";
import { viewFor } from "../../components/operationViews";

function DeleteControl({ generation }: { generation: Generation }) {
  const [confirming, setConfirming] = useState(false);
  const remove = useDeleteGeneration();
  // The server refuses to delete a generation in progress (409): cancel it first.
  const inProgress = !isTerminal(generation.status);
  if (!confirming) {
    return (
      <button
        type="button"
        className="btn-secondary"
        disabled={inProgress}
        title={inProgress ? "Can't delete while it is in progress" : undefined}
        aria-label={`Delete ${generation.title}`}
        onClick={() => {
          setConfirming(true);
        }}
      >
        Delete
      </button>
    );
  }
  return (
    <div role="group" aria-label={`Confirm deleting ${generation.title}`} className="flex flex-wrap items-center gap-2">
      <span className="text-sm">Delete “{generation.title}” and its files?</span>
      <button
        type="button"
        className="btn-secondary border-red-400 text-red-700 dark:text-red-300"
        disabled={remove.isPending}
        onClick={() => {
          remove.mutate(generation.id);
        }}
      >
        Confirm delete
      </button>
      <button
        type="button"
        className="btn-secondary"
        onClick={() => {
          setConfirming(false);
          remove.reset();
        }}
      >
        Cancel
      </button>
      {remove.error && <ErrorMessage error={asUiError(remove.error)} />}
    </div>
  );
}

function LibraryRow({ generation }: { generation: Generation }) {
  const { Summary } = viewFor(generation.operation);
  const audio = generation.status === "succeeded" ? primaryAudio(generation.artifacts) : undefined;
  return (
    <li className="card space-y-2" data-testid="library-row">
      <div className="flex flex-wrap items-center gap-3">
        <Link to={`/library/${generation.id}`} className="font-semibold underline">
          {generation.title}
        </Link>
        <StatusChip status={generation.status} />
        <time className="text-sm text-slate-500" dateTime={generation.created_at}>
          {formatDateTime(generation.created_at)}
        </time>
      </div>
      <Summary generation={generation} />
      {audio && (
        <audio controls preload="none" src={audio.url} aria-label={`Play ${generation.title}`} className="w-full" />
      )}
      <div className="flex flex-wrap gap-2">
        <ReuseButton generation={generation} />
        <CancelButton generation={generation} label={`Cancel ${generation.title}`} />
        <DeleteControl generation={generation} />
      </div>
    </li>
  );
}

export function LibraryPage() {
  const list = useGenerationList();
  const items = list.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Library</h1>
      {list.isPending && <p>Loading…</p>}
      {list.isError && <ErrorMessage error={asUiError(list.error)} />}
      {list.isSuccess && items.length === 0 && (
        <p>
          No songs yet.{" "}
          <Link to="/create" className="underline">
            Create your first one
          </Link>
          .
        </p>
      )}
      {items.length > 0 && (
        <ul className="space-y-3" aria-label="Generations">
          {items.map((g) => (
            <LibraryRow key={g.id} generation={g} />
          ))}
        </ul>
      )}
      {list.hasNextPage && (
        <button
          type="button"
          className="btn-secondary"
          disabled={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
        >
          Load more
        </button>
      )}
    </div>
  );
}
