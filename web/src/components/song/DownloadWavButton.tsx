import { useState } from "react";
import { asUiError, type GenerationArtifact, type UiErrorInfo } from "../../api/client";
import { ErrorMessage } from "../ErrorMessage";
import { audioToWav, wavFileName } from "./wav";

/** Fetches the stored song audio, converts it to a 24-bit WAV in the browser and saves it. */
export function DownloadWavButton({ audio }: { audio: GenerationArtifact }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<UiErrorInfo | null>(null);

  const download = async () => {
    setBusy(true);
    setError(null);
    try {
      // Resolved like the API client's baseUrl: artifact URLs are server-relative.
      const response = await globalThis.fetch(new URL(audio.url, window.location.origin));
      if (!response.ok) throw new Error(`Downloading ${audio.name} failed: HTTP ${String(response.status)}`);
      const wav = await audioToWav(await response.arrayBuffer());
      const href = URL.createObjectURL(new Blob([wav as BlobPart], { type: "audio/wav" }));
      const link = document.createElement("a");
      link.href = href;
      link.download = wavFileName(audio.name);
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => {
        URL.revokeObjectURL(href);
      }, 0);
    } catch (e) {
      setError(asUiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <button
        type="button"
        className="btn-primary"
        disabled={busy}
        onClick={() => {
          void download();
        }}
      >
        {busy ? "Preparing WAV…" : "Download WAV"}
      </button>
      {error && <ErrorMessage error={error} summary="The WAV file could not be made." />}
    </div>
  );
}
