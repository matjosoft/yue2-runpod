import { ArtifactList, primaryAudio } from "../artifacts";
import type { OperationViewProps } from "../operationViews";
import { DownloadWavButton } from "./DownloadWavButton";
import { ScoreView } from "./ScoreView";

/** The succeeded view for song.generate: player, Download WAV (converted in the browser from
 * the stored FLAC), the score, and every stored file, the FLAC included. */
export function SongResult({ generation }: OperationViewProps) {
  const audio = primaryAudio(generation.artifacts);
  return (
    <div className="space-y-3">
      {audio ? (
        <>
          <audio controls preload="metadata" src={audio.url} aria-label={`Play ${generation.title}`} className="w-full" />
          <DownloadWavButton audio={audio} />
        </>
      ) : (
        <p>The audio file is missing.</p>
      )}
      <ScoreView generation={generation} />
      <div>
        <h3 className="font-semibold">Files</h3>
        <ArtifactList artifacts={generation.artifacts} />
      </div>
    </div>
  );
}
