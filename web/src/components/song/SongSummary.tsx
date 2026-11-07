import type { OperationViewProps } from "../operationViews";

export const STYLE_PREVIEW_CHARS = 80;

/** Library row content for song.generate: the first 80 characters of the style. */
export function SongSummary({ generation }: OperationViewProps) {
  const style = typeof generation.request["style"] === "string" ? generation.request["style"] : "";
  const preview =
    style.length > STYLE_PREVIEW_CHARS ? `${style.slice(0, STYLE_PREVIEW_CHARS)}…` : style;
  return <span className="text-sm text-slate-600 dark:text-slate-300">{preview}</span>;
}
