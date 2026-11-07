import { useEffect, useId, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router";
import { asUiError, type SettingsUpdate, type WorkerCheck, type WorkerCheckState } from "../../api/client";
import { recheckWorker, useSaveSettings, useSettings, useWorkerCheck } from "../../api/queries";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ERROR_MESSAGES } from "../../components/errorMessages";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** PRD section 9: the worker URL must be https://, or http:// for localhost only. Blank
 * clears the saved URL (the environment default then applies). */
export function validateWorkerUrl(value: string): string | null {
  const text = value.trim();
  if (!text) return null;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return "Enter a full URL that starts with https://.";
  }
  if (url.protocol === "https:") return null;
  if (url.protocol === "http:" && LOCAL_HOSTS.has(url.hostname)) return null;
  return "Use https://, or http:// only for localhost.";
}

export const CHECK_TEXT: Record<WorkerCheckState, string> = {
  ready: "Connected. The model is ready.",
  warming_up: "Connected. The model is still loading.",
  not_configured: ERROR_MESSAGES.not_configured,
  unreachable: ERROR_MESSAGES.worker_unreachable,
  unauthorized: ERROR_MESSAGES.unauthorized,
  incompatible: ERROR_MESSAGES.incompatible_worker,
  error: "The model failed to load on the worker.",
};

function CheckResult({ check }: { check: WorkerCheck }) {
  if (check.error) return <ErrorMessage error={check.error} summary={CHECK_TEXT[check.state]} />;
  return (
    <p role="status" className="rounded-md bg-green-50 p-3 text-green-900 dark:bg-green-950 dark:text-green-100">
      {CHECK_TEXT[check.state]}
    </p>
  );
}

export function SettingsDialog() {
  const settings = useSettings();
  const save = useSaveSettings();
  const check = useWorkerCheck();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const titleId = useId();
  const urlHelpId = useId();
  const tokenHelpId = useId();
  const [url, setUrl] = useState<string | null>(null); // null: not edited yet
  const [token, setToken] = useState("");
  const [urlError, setUrlError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const urlValue = url ?? settings.data?.worker_url ?? "";
  const tokenSet = settings.data?.token_set ?? false;
  // Until the saved settings have loaded, the URL field is not known, and saving would
  // clear the saved URL.
  const busy = save.isPending || check.isPending || !settings.isSuccess;

  const close = () => {
    // Go back to the page the dialog was opened from, if there is one.
    if (location.key !== "default") void navigate(-1);
    else void navigate("/");
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  });

  async function persist(): Promise<boolean> {
    const error = validateWorkerUrl(urlValue);
    setUrlError(error);
    setSaved(false);
    if (error) return false;
    const body: SettingsUpdate = { worker_url: urlValue.trim() || null };
    if (token) body.token = token; // blank keeps the saved token
    try {
      await save.mutateAsync(body);
    } catch {
      return false; // shown from save.error
    }
    setToken("");
    setUrl(null);
    setSaved(true);
    return true;
  }

  const onSave = (event: FormEvent) => {
    event.preventDefault();
    check.reset();
    void persist().then((ok) => {
      if (ok) recheckWorker(queryClient);
    });
  };

  const onTest = async () => {
    check.reset();
    if (await persist()) {
      check.mutate();
    }
  };

  return (
    <div className="fixed inset-0 z-10 flex items-start justify-center bg-black/40 p-4 pt-20">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl dark:bg-slate-900"
      >
        <h2 id={titleId} className="mb-4 text-xl font-semibold">
          Settings
        </h2>
        <form onSubmit={onSave} noValidate className="space-y-4">
          <div>
            <label htmlFor="worker-url" className="font-medium">
              Worker URL
            </label>
            <input
              id="worker-url"
              className="field"
              type="url"
              autoFocus
              autoComplete="off"
              value={urlValue}
              aria-describedby={urlHelpId}
              aria-invalid={urlError ? true : undefined}
              onChange={(e) => {
                setUrl(e.target.value);
              }}
            />
            <p id={urlHelpId} className="mt-1 text-sm text-slate-500">
              The RunPod proxy URL of your worker (https://), or http://localhost for a local worker.
            </p>
            {urlError && <p className="field-error">{urlError}</p>}
          </div>
          <div>
            <label htmlFor="worker-token" className="font-medium">
              Token
            </label>
            <input
              id="worker-token"
              className="field"
              type="password"
              autoComplete="off"
              placeholder={tokenSet ? "saved" : ""}
              value={token}
              aria-describedby={tokenHelpId}
              onChange={(e) => {
                setToken(e.target.value);
              }}
            />
            <p id={tokenHelpId} className="mt-1 text-sm text-slate-500">
              {tokenSet ? "A token is saved. Leave this blank to keep it." : "No token saved yet."}
            </p>
          </div>
          {settings.isError && <ErrorMessage error={asUiError(settings.error)} />}
          {save.error && <ErrorMessage error={asUiError(save.error)} />}
          {check.error && <ErrorMessage error={asUiError(check.error)} />}
          {check.data && <CheckResult check={check.data} />}
          {saved && !check.data && !check.isPending && <p role="status">Settings saved.</p>}
          {check.isPending && <p role="status">Testing the connection…</p>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => void onTest()}>
              Test connection
            </button>
            <button type="submit" className="btn-primary" disabled={busy}>
              Save
            </button>
            <button type="button" className="btn-secondary ml-auto" onClick={close}>
              Close
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
