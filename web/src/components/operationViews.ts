// PRD section 7: maps an operation name to its Summary and Result components. The library
// and the result card render through this registry, so a new operation ships its own
// views without touching them. This and the worker registry are the only places that
// may branch on an operation name.
import type { ComponentType } from "react";
import type { Generation } from "../api/client";
import { GenericResult, GenericSummary } from "./GenericView";
import { SongResult } from "./song/SongResult";
import { SongSummary } from "./song/SongSummary";

export interface OperationViewProps {
  generation: Generation;
}

export interface OperationView {
  /** One line of operation-specific content in a library row. */
  Summary: ComponentType<OperationViewProps>;
  /** The body of a succeeded generation. */
  Result: ComponentType<OperationViewProps>;
}

export const operationViews: Record<string, OperationView> = {
  "song.generate": { Summary: SongSummary, Result: SongResult },
};

export const fallbackView: OperationView = { Summary: GenericSummary, Result: GenericResult };

export function viewFor(operation: string): OperationView {
  return operationViews[operation] ?? fallbackView;
}
