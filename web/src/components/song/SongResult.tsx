import { ArtifactList, primaryAudio } from "../artifacts";
import type { OperationViewProps } from "../operationViews";

/** The succeeded view for song.generate: player, Download FLAC, and the other files. */
export function SongResult({ generation }: OperationViewProps) {
  const audio = primaryAudio(generation.artifacts);
  const others = generation.artifacts.filter((a) => a !== audio);
  return (
    <div className="space-y-3">
      {audio ? (
        <>
          <audio controls preload="metadata" src={audio.url} aria-label={`Play ${generation.title}`} className="w-full" />
          <a className="btn-primary inline-block" href={audio.url} download={audio.name.split("/").pop() ?? audio.name}>
            {audio.media_type === "audio/flac" ? "Download FLAC" : "Download audio"}
          </a>
        </>
      ) : (
        <p>The audio file is missing.</p>
      )}
      <div>
        <h3 className="font-semibold">Files</h3>
        <ArtifactList artifacts={others} />
      </div>
    </div>
  );
}
