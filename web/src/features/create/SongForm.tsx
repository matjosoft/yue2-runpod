import { useId, useState, type FormEvent, type ReactNode } from "react";
import { COT_MODES, EXAMPLE, SCORE_TYPES, type CotMode, type FieldErrors, type SongFormValues } from "./songForm";

interface Props {
  values: SongFormValues;
  errors: FieldErrors;
  submitting: boolean;
  onChange: (values: SongFormValues) => void;
  onSubmit: () => void;
  children?: ReactNode;
}

function Field(props: {
  id: string;
  label: string;
  help?: string;
  error?: string | undefined;
  children: (describedBy: string | undefined, invalid: boolean) => ReactNode;
}) {
  const helpId = `${props.id}-help`;
  const errorId = `${props.id}-error`;
  const describedBy = [props.help ? helpId : null, props.error ? errorId : null].filter(Boolean).join(" ");
  return (
    <div>
      <label htmlFor={props.id} className="font-medium">
        {props.label}
      </label>
      {props.children(describedBy || undefined, Boolean(props.error))}
      {props.help && (
        <p id={helpId} className="mt-1 text-sm text-slate-500">
          {props.help}
        </p>
      )}
      {props.error && (
        <p id={errorId} className="field-error">
          {props.error}
        </p>
      )}
    </div>
  );
}

export function SongForm({ values, errors, submitting, onChange, onSubmit, children }: Props) {
  const id = useId();
  const hasScore = values.abc.trim() !== "";
  // The score section opens by itself when it has content or an error, and otherwise
  // remembers what the user last did with it. React only writes ``open`` when the
  // computed value changes, so the user can still close it while it has content.
  const [scoreOpen, setScoreOpen] = useState(false);
  const set = <K extends keyof SongFormValues>(key: K, value: SongFormValues[K]) => {
    onChange({ ...values, [key]: value });
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };
  return (
    <form onSubmit={submit} noValidate className="space-y-4" aria-label="Create a song">
      <Field id={`${id}-title`} label="Title" help="Optional. Blank means “Untitled” with the date and time." error={errors.title}>
        {(describedBy, invalid) => (
          <input
            id={`${id}-title`}
            className="field"
            value={values.title}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            onChange={(e) => {
              set("title", e.target.value);
            }}
          />
        )}
      </Field>
      <Field
        id={`${id}-style`}
        label="Style"
        help="Genre, instruments, vocal character, language, tempo."
        error={errors.style}
      >
        {(describedBy, invalid) => (
          <textarea
            id={`${id}-style`}
            className="field"
            rows={2}
            value={values.style}
            aria-required="true"
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            onChange={(e) => {
              set("style", e.target.value);
            }}
          />
        )}
      </Field>
      <Field
        id={`${id}-lyrics`}
        label="Lyrics"
        help="Mark sections with tags such as [Verse] and [Chorus], with an empty line between sections."
        error={errors.lyrics}
      >
        {(describedBy, invalid) => (
          <textarea
            id={`${id}-lyrics`}
            className="field font-mono"
            rows={12}
            value={values.lyrics}
            aria-required="true"
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            onChange={(e) => {
              set("lyrics", e.target.value);
            }}
          />
        )}
      </Field>
      <details
        className="card"
        open={scoreOpen || hasScore || Boolean(errors.abc)}
        onToggle={(e) => {
          setScoreOpen(e.currentTarget.open);
        }}
      >
        <summary className="cursor-pointer font-medium">Score (ABC){hasScore ? " · provided" : ""}</summary>
        <div className="mt-3 space-y-4">
          <Field
            id={`${id}-abc`}
            label="Score (ABC)"
            help="Optional. An ABC score for the model to sing instead of writing its own. Blank means the model plans one. “Edit score” on a finished song puts its score here."
            error={errors.abc}
          >
            {(describedBy, invalid) => (
              <textarea
                id={`${id}-abc`}
                className="field font-mono text-sm"
                rows={10}
                spellCheck={false}
                value={values.abc}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                onChange={(e) => {
                  set("abc", e.target.value);
                }}
              />
            )}
          </Field>
          {hasScore && (
            <fieldset>
              <legend className="font-medium">Score type</legend>
              <div className="mt-1 flex flex-wrap gap-x-6 gap-y-1">
                {SCORE_TYPES.map((t) => (
                  <label key={t.cot} className="flex items-center gap-2">
                    <input
                      type="radio"
                      name={`${id}-score-type`}
                      value={t.cot}
                      checked={values.cot === t.cot}
                      onChange={() => {
                        set("cot", t.cot);
                      }}
                    />
                    {t.label}
                  </label>
                ))}
              </div>
              <p className="mt-1 text-sm text-slate-500">
                Full means the score has chord symbols; melody only means it has none. This sets the planning mode.
              </p>
            </fieldset>
          )}
          {hasScore && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                set("abc", "");
              }}
            >
              Clear score
            </button>
          )}
        </div>
      </details>
      <details className="card" open={Boolean(errors.seed ?? errors.cot ?? errors.cfg_scale) || undefined}>
        <summary className="cursor-pointer font-medium">Advanced</summary>
        <div className="mt-3 space-y-4">
          <Field id={`${id}-seed`} label="Seed" help="Blank means random." error={errors.seed}>
            {(describedBy, invalid) => (
              <input
                id={`${id}-seed`}
                className="field"
                inputMode="numeric"
                value={values.seed}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                onChange={(e) => {
                  set("seed", e.target.value);
                }}
              />
            )}
          </Field>
          <Field id={`${id}-cot`} label="Planning mode" help="full plans melody and structure; melody plans the melody only; off skips planning." error={errors.cot}>
            {(describedBy) => (
              <select
                id={`${id}-cot`}
                className="field"
                value={values.cot}
                aria-describedby={describedBy}
                onChange={(e) => {
                  set("cot", e.target.value as CotMode);
                }}
              >
                {COT_MODES.map((mode) => (
                  // A score needs planning, so "off" is not offered while there is one.
                  <option key={mode} value={mode} disabled={hasScore && mode === "off"}>
                    {mode}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field id={`${id}-cfg`} label="CFG scale" help="Optional. Blank means the model default." error={errors.cfg_scale}>
            {(describedBy, invalid) => (
              <input
                id={`${id}-cfg`}
                className="field"
                inputMode="decimal"
                value={values.cfgScale}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                onChange={(e) => {
                  set("cfgScale", e.target.value);
                }}
              />
            )}
          </Field>
        </div>
      </details>
      <div className="flex gap-2">
        <button type="submit" className="btn-primary" disabled={submitting}>
          Create
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            onChange({ ...values, style: EXAMPLE.style, lyrics: EXAMPLE.lyrics });
          }}
        >
          Example
        </button>
      </div>
      {children}
    </form>
  );
}
