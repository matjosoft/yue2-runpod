import { useId, type FormEvent, type ReactNode } from "react";
import { COT_MODES, EXAMPLE, type CotMode, type FieldErrors, type SongFormValues } from "./songForm";

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
                  <option key={mode} value={mode}>
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
