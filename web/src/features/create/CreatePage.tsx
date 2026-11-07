import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { asUiError, type Generation, type GenerationCreate, type UiErrorInfo } from "../../api/client";
import { useCreateGeneration } from "../../api/queries";
import { loadDraft, onDraftReused, saveDraft } from "../../components/draft";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ERROR_MESSAGES, requestFieldErrors } from "../../components/errorMessages";
import { ResultCard } from "./ResultCard";
import { SongForm } from "./SongForm";
import { buildCreate, FORM_FIELDS, fromDraft, toDraft, type FieldErrors, type SongFormValues } from "./songForm";

export function CreatePage() {
  const [search, setSearch] = useSearchParams();
  const generationId = search.get("gen"); // in the URL, so a reload resumes progress
  const create = useCreateGeneration();
  const [values, setValues] = useState<SongFormValues>(() => fromDraft(loadDraft()));
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [submitError, setSubmitError] = useState<UiErrorInfo | null>(null);

  useEffect(() => {
    saveDraft(toDraft(values));
  }, [values]);

  useEffect(
    () =>
      onDraftReused((draft) => {
        setValues(fromDraft(draft));
        setFieldErrors({});
        setSubmitError(null);
      }),
    [],
  );

  const submit = async (body: GenerationCreate) => {
    setSubmitError(null);
    try {
      const generation = await create.mutateAsync(body);
      setSearch({ gen: generation.id });
    } catch (error) {
      // The form keeps what the user typed (PRD section 9).
      const info = asUiError(error);
      setSubmitError(info);
      setFieldErrors(requestFieldErrors(info));
    }
  };

  const onSubmit = () => {
    const { body, errors } = buildCreate(values);
    setFieldErrors(errors);
    setSubmitError(null);
    if (body) void submit(body);
  };

  const onRetry = (generation: Generation) => {
    void submit({ title: generation.title, operation: generation.operation, request: generation.request });
  };

  // When every field message is shown inline, the banner only needs a summary line.
  const fieldKeys = Object.keys(fieldErrors);
  const allInline =
    submitError?.code === "validation_error" &&
    fieldKeys.length > 0 &&
    fieldKeys.every((k) => (FORM_FIELDS as readonly string[]).includes(k));

  return (
    <div className="grid gap-6 md:grid-cols-2">
      <div>
        <h1 className="mb-4 text-2xl font-semibold">Create</h1>
        <SongForm
          values={values}
          errors={fieldErrors}
          submitting={create.isPending}
          onChange={setValues}
          onSubmit={onSubmit}
        >
          {submitError && (
            <ErrorMessage error={submitError} {...(allInline ? { summary: ERROR_MESSAGES.validation_error } : {})} />
          )}
        </SongForm>
      </div>
      <ResultCard generationId={generationId} submitting={create.isPending} onRetry={onRetry} />
    </div>
  );
}
