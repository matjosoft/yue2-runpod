import type { GenerationArtifact } from "../api/client";
import { formatBytes } from "./format";

/** The artifact the UI plays, chosen by role (never by file name). */
export function primaryAudio(artifacts: GenerationArtifact[]): GenerationArtifact | undefined {
  return artifacts.find((a) => a.role === "primary_audio");
}

export function ArtifactList({ artifacts }: { artifacts: GenerationArtifact[] }) {
  if (artifacts.length === 0) return <p className="text-sm text-slate-500">No files.</p>;
  return (
    <ul className="text-sm">
      {artifacts.map((a) => (
        <li key={a.name} className="flex justify-between gap-4 py-0.5">
          <a className="underline" href={a.url} download={a.name.split("/").pop() ?? a.name}>
            {a.name}
          </a>
          <span className="text-slate-500">{formatBytes(a.size_bytes)}</span>
        </li>
      ))}
    </ul>
  );
}
