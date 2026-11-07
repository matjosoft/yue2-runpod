/** Renders the stages the operation declared, in order, with the current one marked. The
 * UI never hardcodes stage names (PRD section 6). */
export function StageList({ stages, current }: { stages: string[]; current: string | null }) {
  const index = current === null ? -1 : stages.indexOf(current);
  return (
    <ol aria-label="Stages" className="space-y-1">
      {stages.map((stage, i) => {
        const state = i < index ? "done" : i === index ? "current" : "pending";
        return (
          <li
            key={stage}
            data-state={state}
            aria-current={state === "current" ? "step" : undefined}
            className={state === "current" ? "font-semibold" : state === "done" ? "text-slate-500" : "text-slate-400"}
          >
            <span aria-hidden="true" className="inline-block w-5">
              {state === "done" ? "✓" : state === "current" ? "▶" : "·"}
            </span>
            {stage}
          </li>
        );
      })}
    </ol>
  );
}
