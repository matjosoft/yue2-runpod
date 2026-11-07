import { useId, useState } from "react";
import type { UiErrorInfo } from "../api/client";
import { plainMessage } from "./errorMessages";

/** A plain-language error with a Details toggle that shows the code and raw message.
 * ``summary`` replaces the plain message when the caller has a better one. */
export function ErrorMessage({ error, summary }: { error: UiErrorInfo; summary?: string }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  return (
    <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100">
      <p className="whitespace-pre-line">{summary ?? plainMessage(error)}</p>
      <button
        type="button"
        className="mt-1 text-sm underline"
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={() => {
          setOpen((v) => !v);
        }}
      >
        Details
      </button>
      {open && (
        <dl id={detailsId} className="mt-1 text-sm">
          <dt className="font-semibold">Code</dt>
          <dd>
            <code>{error.code}</code>
          </dd>
          <dt className="font-semibold">Message</dt>
          <dd>{error.message}</dd>
        </dl>
      )}
    </div>
  );
}
