import type { OperationViewProps } from "./operationViews";
import { ArtifactList } from "./artifacts";

/** Used for an operation with no registered view, so a new operation still renders. */
export function GenericSummary({ generation }: OperationViewProps) {
  return <span className="text-sm text-slate-500">{generation.operation}</span>;
}

export function GenericResult({ generation }: OperationViewProps) {
  return (
    <div>
      <h3 className="font-semibold">Files</h3>
      <ArtifactList artifacts={generation.artifacts} />
    </div>
  );
}
