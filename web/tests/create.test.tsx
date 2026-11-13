import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { GENERATION_POLL_MS } from "../src/api/queries";
import { loadDraft, saveDraft } from "../src/components/draft";
import { EXAMPLE } from "../src/features/create/songForm";
import {
  envelope,
  installDefaultApi,
  makeGeneration,
  SCORE_TEXT,
  succeededGeneration,
  type FakeApi,
} from "./fakeApi";
import { renderApp } from "./render";

async function fill(label: string, value: string) {
  const field = screen.getByLabelText(label);
  await userEvent.clear(field);
  await userEvent.click(field);
  await userEvent.paste(value); // paste: "[" is a key descriptor for userEvent.type
}

function createRoute(fake: FakeApi, id = "gen_01NEW") {
  fake.on("POST", "/api/generations", (req) => {
    const body = req.body as { title?: string; request: Record<string, unknown> };
    return {
      status: 201,
      body: makeGeneration({ id, request: { seed: 777, ...body.request }, ...(body.title ? { title: body.title } : {}) }),
    };
  });
  fake.on("GET", `/api/generations/${id}`, { body: makeGeneration({ id }) });
}

describe("Create form (U-1, U-7)", () => {
  test("AT-2 UI: empty style or lyrics blocks submit with an inline message", async () => {
    const fake = installDefaultApi();
    renderApp("/create");
    await userEvent.click(await screen.findByRole("button", { name: "Create" }));
    expect(screen.getByText("Style is required.")).toBeInTheDocument();
    expect(screen.getByText("Lyrics are required.")).toBeInTheDocument();
    expect(screen.getByLabelText("Style")).toHaveAttribute("aria-invalid", "true");

    await fill("Style", "jazz");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.queryByText("Style is required.")).not.toBeInTheDocument();
    expect(screen.getByText("Lyrics are required.")).toBeInTheDocument();
    expect(fake.callsTo("POST", "/api/generations")).toHaveLength(0);
  });

  test("Example gives a valid, submittable form (U-7)", async () => {
    const fake = installDefaultApi();
    createRoute(fake);
    renderApp("/create");
    await userEvent.click(await screen.findByRole("button", { name: "Example" }));
    expect(screen.getByLabelText("Style")).toHaveValue(EXAMPLE.style);
    expect(screen.getByLabelText("Lyrics")).toHaveValue(EXAMPLE.lyrics);
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => {
      expect(fake.callsTo("POST", "/api/generations")).toHaveLength(1);
    });
    expect(fake.callsTo("POST", "/api/generations")[0]?.body).toEqual({
      operation: "song.generate",
      request: { style: EXAMPLE.style, lyrics: EXAMPLE.lyrics, cot: "full" },
    });
    // The active generation goes into the URL, so a reload resumes it (U-2).
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/create?gen=gen_01NEW");
    });
  });

  test("advanced fields: title, seed, planning mode and CFG scale are sent", async () => {
    const fake = installDefaultApi();
    createRoute(fake);
    renderApp("/create");
    await screen.findByRole("button", { name: "Create" });
    await fill("Title", "My song");
    await fill("Style", "rock");
    await fill("Lyrics", "[Verse]\nhey");
    await fill("Seed", "42");
    await userEvent.selectOptions(screen.getByLabelText("Planning mode"), "melody");
    await fill("CFG scale", "1.5");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => {
      expect(fake.callsTo("POST", "/api/generations")).toHaveLength(1);
    });
    expect(fake.callsTo("POST", "/api/generations")[0]?.body).toEqual({
      title: "My song",
      operation: "song.generate",
      request: { style: "rock", lyrics: "[Verse]\nhey", cot: "melody", seed: 42, cfg_scale: 1.5 },
    });
  });

  test("a score is sent verbatim with the chosen score type", async () => {
    const fake = installDefaultApi();
    createRoute(fake);
    renderApp("/create");
    await screen.findByRole("button", { name: "Create" });
    await fill("Style", "folk");
    await fill("Lyrics", "[Verse]\nhey");
    expect(screen.queryByRole("radio", { name: "Melody only" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("Score (ABC)", { selector: "summary" }));
    const score = "X:1\nM:6/8\nL:1/8\nK:D\n|: dfa afd :|\n";
    await fill("Score (ABC)", score);
    // Full is the default planning mode; a score lets the user say it is melody only.
    expect(screen.getByRole("radio", { name: "Full score (melody and chords)" })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "Melody only" }));
    expect(screen.getByLabelText("Planning mode")).toHaveValue("melody");
    expect(within(screen.getByLabelText("Planning mode")).getByRole("option", { name: "off" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => {
      expect(fake.callsTo("POST", "/api/generations")).toHaveLength(1);
    });
    expect(fake.callsTo("POST", "/api/generations")[0]?.body).toEqual({
      operation: "song.generate",
      request: { style: "folk", lyrics: "[Verse]\nhey", cot: "melody", abc: score },
    });
  });

  test("a blank score is left out of the request", async () => {
    const fake = installDefaultApi();
    createRoute(fake);
    renderApp("/create");
    await screen.findByRole("button", { name: "Create" });
    await fill("Style", "folk");
    await fill("Lyrics", "la");
    await userEvent.click(screen.getByText("Score (ABC)", { selector: "summary" }));
    await fill("Score (ABC)", "  \n ");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => {
      expect(fake.callsTo("POST", "/api/generations")).toHaveLength(1);
    });
    expect(fake.callsTo("POST", "/api/generations")[0]?.body).toEqual({
      operation: "song.generate",
      request: { style: "folk", lyrics: "la", cot: "full" },
    });
  });

  test("a score with planning mode off is refused inline", async () => {
    const fake = installDefaultApi();
    renderApp("/create");
    await screen.findByRole("button", { name: "Create" });
    await fill("Style", "folk");
    await fill("Lyrics", "la");
    await userEvent.selectOptions(screen.getByLabelText("Planning mode"), "off");
    await userEvent.click(screen.getByText("Score (ABC)", { selector: "summary" }));
    await fill("Score (ABC)", "X:1\nK:C\nC|");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByText("Choose whether the score is a full score or a melody only.")).toBeInTheDocument();
    expect(screen.getByLabelText("Score (ABC)")).toHaveAttribute("aria-invalid", "true");
    expect(fake.callsTo("POST", "/api/generations")).toHaveLength(0);
  });

  test("a 422 for the score shows the field message on the score", async () => {
    const fake = installDefaultApi();
    fake.on("POST", "/api/generations", {
      status: 422,
      body: envelope("validation_error", "Invalid params", {
        fields: [{ loc: "request.abc", msg: "String should have at most 16000 characters" }],
      }),
    });
    renderApp("/create");
    await screen.findByRole("button", { name: "Create" });
    await fill("Style", "folk");
    await fill("Lyrics", "la");
    await userEvent.click(screen.getByText("Score (ABC)", { selector: "summary" }));
    await fill("Score (ABC)", "X:1\nK:C\nC|");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("String should have at most 16000 characters")).toBeInTheDocument();
    expect(screen.getByLabelText("Score (ABC)")).toHaveValue("X:1\nK:C\nC|");
    expect(screen.getByRole("alert")).toHaveTextContent("Some fields are not valid.");
  });

  test("a seed that is not a whole number is refused inline", async () => {
    const fake = installDefaultApi();
    renderApp("/create");
    await screen.findByRole("button", { name: "Create" });
    await fill("Style", "rock");
    await fill("Lyrics", "la");
    await fill("Seed", "12.5");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByText("Seed must be a whole number, or blank for random.")).toBeInTheDocument();
    expect(fake.callsTo("POST", "/api/generations")).toHaveLength(0);
  });

  test("AT-2 UI: a 422 for an over-limit style shows the field message", async () => {
    const fake = installDefaultApi();
    fake.on("POST", "/api/generations", {
      status: 422,
      body: envelope("validation_error", "Invalid params", {
        fields: [{ loc: "request.style", msg: "String should have at most 1000 characters" }],
      }),
    });
    renderApp("/create");
    await screen.findByRole("button", { name: "Create" });
    const longStyle = "x".repeat(1001);
    await fill("Style", longStyle);
    await fill("Lyrics", "la");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("String should have at most 1000 characters")).toBeInTheDocument();
    expect(screen.getByLabelText("Style")).toHaveValue(longStyle);
    expect(screen.getByRole("alert")).toHaveTextContent("Some fields are not valid.");
  });

  test("AT-3 UI: worker unreachable shows the plain message and keeps the input", async () => {
    const fake = installDefaultApi();
    fake.on("POST", "/api/generations", { status: 502, body: envelope("worker_unreachable", "connect timeout") });
    renderApp("/create");
    await screen.findByRole("button", { name: "Create" });
    await fill("Title", "Keep me");
    await fill("Style", "soul");
    await fill("Lyrics", "[Chorus]\nooh");
    await fill("Seed", "9");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Can't reach the GPU worker. Is the pod running?")).toBeInTheDocument();
    expect(screen.getByLabelText("Title")).toHaveValue("Keep me");
    expect(screen.getByLabelText("Style")).toHaveValue("soul");
    expect(screen.getByLabelText("Lyrics")).toHaveValue("[Chorus]\nooh");
    expect(screen.getByLabelText("Seed")).toHaveValue("9");
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/create$/);
    expect(screen.getByText("Your song will appear here")).toBeInTheDocument();
  });

  test("not_configured points to Settings", async () => {
    const fake = installDefaultApi();
    fake.on("POST", "/api/generations", { status: 409, body: envelope("not_configured", "no url") });
    renderApp("/create");
    await userEvent.click(await screen.findByRole("button", { name: "Example" }));
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Add your worker URL and token in Settings.")).toBeInTheDocument();
  });

  test("the draft is kept in localStorage and restored on load", async () => {
    installDefaultApi();
    const first = renderApp("/create");
    await screen.findByRole("button", { name: "Create" });
    await fill("Style", "lofi");
    await fill("Seed", "5");
    expect(loadDraft()?.request).toMatchObject({ style: "lofi", seed: "5" });
    first.unmount();

    renderApp("/create");
    await screen.findByRole("button", { name: "Create" });
    expect(screen.getByLabelText("Style")).toHaveValue("lofi");
    expect(screen.getByLabelText("Seed")).toHaveValue("5");
  });

  test("a stored draft with numbers fills the form", async () => {
    installDefaultApi();
    saveDraft({ title: "", request: { style: "a", lyrics: "b", seed: 12345, cot: "off", cfg_scale: 2 } });
    renderApp("/create");
    await screen.findByRole("button", { name: "Create" });
    expect(screen.getByLabelText("Seed")).toHaveValue("12345");
    expect(screen.getByLabelText("Planning mode")).toHaveValue("off");
    expect(screen.getByLabelText("CFG scale")).toHaveValue("2");
  });
});

describe("result card (U-2, U-3, U-6)", () => {
  test("idle shows the placeholder", async () => {
    installDefaultApi();
    renderApp("/create");
    expect(await screen.findByText("Your song will appear here")).toBeInTheDocument();
  });

  test("submitting shows a spinner and disables Create", async () => {
    const fake = installDefaultApi();
    let release: () => void = () => undefined;
    fake.on("POST", "/api/generations", () => new Promise((resolve) => {
      release = () => {
        resolve({ status: 201, body: makeGeneration() });
      };
    }));
    renderApp("/create");
    await userEvent.click(await screen.findByRole("button", { name: "Example" }));
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Submitting…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
    release();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create" })).toBeEnabled();
    });
  });

  test.each([
    ["queued", "Waiting for the GPU"],
    ["downloading", "Fetching your song"],
    ["canceled", "This generation was canceled."],
  ] as const)("%s shows %s", async (status, text) => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: makeGeneration({ status }) });
    renderApp("/create?gen=gen_01TEST");
    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  test("running shows the declared stages in order, the current one marked, and a timer", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", {
      body: makeGeneration({ status: "running", stage: "synthesis", stages: ["plan", "semantic", "synthesis", "decode", "save"] }),
    });
    renderApp("/create?gen=gen_01TEST");
    const list = await screen.findByRole("list", { name: "Stages" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((li) => li.textContent?.slice(1))).toEqual(["plan", "semantic", "synthesis", "decode", "save"]);
    expect(items.map((li) => li.dataset["state"])).toEqual(["done", "done", "current", "pending", "pending"]);
    expect(items[2]).toHaveAttribute("aria-current", "step");
    expect(screen.getByTestId("elapsed")).toHaveTextContent(/^\d+:\d\d$/);
    expect(screen.getByTestId("result-status")).toHaveAttribute("aria-live", "polite");
  });

  test("renders whatever stages the operation declares (no hardcoded names)", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", {
      body: makeGeneration({ status: "running", stage: "generate", stages: ["generate", "save"] }),
    });
    renderApp("/create?gen=gen_01TEST");
    const list = await screen.findByRole("list", { name: "Stages" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText("generate").closest("li")).toHaveAttribute("aria-current", "step");
  });

  test("polls every 1.5 s until terminal, then stops (U-2)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: makeGeneration({ status: "running", stage: "plan" }) });
    renderApp("/create?gen=gen_01TEST");
    await screen.findByRole("list", { name: "Stages" });
    const calls = () => fake.callsTo("GET", "/api/generations/gen_01TEST").length;
    const before = calls();

    fake.on("GET", "/api/generations/gen_01TEST", { body: makeGeneration({ status: "running", stage: "decode" }) });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(GENERATION_POLL_MS);
    });
    await waitFor(() => {
      expect(screen.getByText("decode").closest("li")).toHaveAttribute("aria-current", "step");
    });
    expect(calls()).toBe(before + 1);

    fake.on("GET", "/api/generations/gen_01TEST", { body: succeededGeneration() });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(GENERATION_POLL_MS);
    });
    await screen.findByRole("button", { name: "Download WAV" });
    const atTerminal = calls();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(GENERATION_POLL_MS * 3);
    });
    expect(calls()).toBe(atTerminal);
  });

  test("succeeded shows the player, Download WAV and every file (U-3)", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: succeededGeneration() });
    const { container } = renderApp("/create?gen=gen_01TEST");
    await screen.findByRole("button", { name: "Download WAV" });
    const audio = container.querySelector("audio");
    expect(audio).toHaveAttribute("src", "/api/generations/gen_01TEST/artifacts/audio.flac");
    expect(audio).toHaveAttribute("controls");
    // The stored FLAC is still downloadable as is, under Files.
    expect(screen.getByRole("link", { name: "audio.flac" })).toHaveAttribute(
      "href",
      "/api/generations/gen_01TEST/artifacts/audio.flac",
    );
    expect(screen.getByRole("link", { name: "score.abc" })).toHaveAttribute(
      "href",
      "/api/generations/gen_01TEST/artifacts/score.abc",
    );
    expect(screen.getByRole("link", { name: "result.json" })).toBeInTheDocument();
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
  });

  test("Download WAV converts the stored audio and saves audio.wav", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: succeededGeneration() });
    fake.on("GET", "/api/generations/gen_01TEST/artifacts/audio.flac", { text: "flac-bytes" });
    const decoded = { numberOfChannels: 2, sampleRate: 48000, getChannelData: () => new Float32Array([0, 0.5]) };
    const contexts: number[] = [];
    vi.stubGlobal(
      "OfflineAudioContext",
      class {
        constructor(_channels: number, _length: number, rate: number) {
          contexts.push(rate);
        }
        decodeAudioData() {
          return Promise.resolve(decoded);
        }
      },
    );
    const blobs: Blob[] = [];
    // jsdom has no object URLs; add them for this test only.
    const { createObjectURL, revokeObjectURL } = URL;
    URL.createObjectURL = (b: Blob) => {
      blobs.push(b);
      return "blob:wav";
    };
    URL.revokeObjectURL = () => undefined;
    const saved: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(`${this.download} ${this.href}`);
    });

    renderApp("/create?gen=gen_01TEST");
    await userEvent.click(await screen.findByRole("button", { name: "Download WAV" }));
    await waitFor(() => {
      expect(saved).toEqual(["audio.wav blob:wav"]);
    });
    expect(fake.callsTo("GET", "/api/generations/gen_01TEST/artifacts/audio.flac")).toHaveLength(1);
    expect(contexts).toEqual([48000]); // not a FLAC header, so the 48 kHz default
    expect(blobs[0]?.type).toBe("audio/wav");
    expect(blobs[0]?.size).toBe(44 + 2 * 2 * 3);
    click.mockRestore();
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
  });

  test("Download WAV says so when the audio cannot be fetched", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: succeededGeneration() });
    fake.on("GET", "/api/generations/gen_01TEST/artifacts/audio.flac", {
      status: 404,
      body: envelope("not_found", "Unknown artifact"),
    });
    renderApp("/create?gen=gen_01TEST");
    await userEvent.click(await screen.findByRole("button", { name: "Download WAV" }));
    expect(await screen.findByText("The WAV file could not be made.")).toBeInTheDocument();
  });

  test("succeeded shows the generated score as text", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: succeededGeneration() });
    renderApp("/create?gen=gen_01TEST");
    expect(await screen.findByTestId("score-text")).toHaveTextContent("K:C");
    expect(screen.getByTestId("score-text").textContent).toBe(SCORE_TEXT);
    expect(fake.callsTo("GET", "/api/generations/gen_01TEST/artifacts/score.abc")).toHaveLength(1);
  });

  test("no score section when the song has no score (planning off)", async () => {
    const fake = installDefaultApi();
    const gen = succeededGeneration();
    fake.on("GET", "/api/generations/gen_01TEST", {
      body: { ...gen, artifacts: gen.artifacts.filter((a) => a.role !== "score") },
    });
    renderApp("/create?gen=gen_01TEST");
    await screen.findByRole("button", { name: "Download WAV" });
    expect(screen.queryByRole("button", { name: "Edit score" })).not.toBeInTheDocument();
  });

  test("a score that fails to load says so", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: succeededGeneration() });
    fake.on("GET", "/api/generations/gen_01TEST/artifacts/score.abc", {
      status: 404,
      body: envelope("not_found", "Unknown artifact"),
    });
    renderApp("/create?gen=gen_01TEST");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit score" })).toBeDisabled();
  });

  test("Edit score fills the form with the song's settings and its score, ready to edit and create", async () => {
    const fake = installDefaultApi();
    createRoute(fake);
    fake.on("GET", "/api/generations/gen_01TEST", {
      body: succeededGeneration({ request: { style: "funk", lyrics: "[Verse]\nyo", seed: 4242, cot: "melody" } }),
    });
    renderApp("/create?gen=gen_01TEST");
    await screen.findByTestId("score-text");
    await userEvent.click(screen.getByRole("button", { name: "Edit score" }));
    expect(screen.getByLabelText("Score (ABC)")).toHaveValue(SCORE_TEXT);
    expect(screen.getByLabelText("Style")).toHaveValue("funk");
    expect(screen.getByLabelText("Seed")).toHaveValue("4242");
    expect(screen.getByRole("radio", { name: "Melody only" })).toBeChecked();

    const edited = SCORE_TEXT.replace("CDEF GABc", "EDCD EEE2");
    await fill("Score (ABC)", edited);
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => {
      expect(fake.callsTo("POST", "/api/generations")).toHaveLength(1);
    });
    expect(fake.callsTo("POST", "/api/generations")[0]?.body).toEqual({
      operation: "song.generate",
      request: { style: "funk", lyrics: "[Verse]\nyo", seed: 4242, cot: "melody", abc: edited },
    });
  });

  test("Edit score on a full-planned song keeps full", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: succeededGeneration() });
    renderApp("/create?gen=gen_01TEST");
    await screen.findByTestId("score-text");
    await userEvent.click(screen.getByRole("button", { name: "Edit score" }));
    expect(screen.getByRole("radio", { name: "Full score (melody and chords)" })).toBeChecked();
    expect(screen.getByLabelText("Planning mode")).toHaveValue("full");
  });

  test("Reuse settings fills the form with the stored request, seed included", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", {
      body: succeededGeneration({ request: { style: "funk", lyrics: "[Verse]\nyo", seed: 4242, cot: "melody", cfg_scale: 3 } }),
    });
    renderApp("/create?gen=gen_01TEST");
    await userEvent.click(await screen.findByRole("button", { name: "Reuse settings" }));
    expect(screen.getByLabelText("Style")).toHaveValue("funk");
    expect(screen.getByLabelText("Lyrics")).toHaveValue("[Verse]\nyo");
    expect(screen.getByLabelText("Seed")).toHaveValue("4242");
    expect(screen.getByLabelText("Planning mode")).toHaveValue("melody");
    expect(screen.getByLabelText("CFG scale")).toHaveValue("3");
  });

  test("failed shows the plain message, a Details toggle, and Retry posts the same request", async () => {
    const fake = installDefaultApi();
    const failed = makeGeneration({
      status: "failed",
      title: "Song A",
      request: { style: "funk", lyrics: "la", seed: 99, cot: "full" },
      error: { code: "worker_restarted", message: "worker restarted while the job ran", retryable: true, details: {} },
    });
    fake.on("GET", "/api/generations/gen_01TEST", { body: failed });
    createRoute(fake, "gen_01RETRY");
    renderApp("/create?gen=gen_01TEST");
    expect(await screen.findByText("The worker restarted during generation. Retry.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("worker_restarted")).toBeInTheDocument();
    expect(screen.getByText("worker restarted while the job ran")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/create?gen=gen_01RETRY");
    });
    expect(fake.callsTo("POST", "/api/generations")[0]?.body).toEqual({
      title: "Song A",
      operation: "song.generate",
      request: { style: "funk", lyrics: "la", seed: 99, cot: "full" },
    });
  });

  test("a failed poll is shown without hiding the last known progress", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: makeGeneration({ status: "running", stage: "plan" }) });
    renderApp("/create?gen=gen_01TEST");
    await screen.findByRole("list", { name: "Stages" });
    fake.on("GET", "/api/generations/gen_01TEST", () => Promise.reject(new TypeError("Failed to fetch")));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(GENERATION_POLL_MS);
    });
    expect(await screen.findByText("Can't reach the app server. Is it running?")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Stages" })).toBeInTheDocument();
  });

  test.each(["queued", "running"] as const)("%s shows a Cancel button", async (status) => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: makeGeneration({ status, stage: status === "running" ? "plan" : null }) });
    renderApp("/create?gen=gen_01TEST");
    expect(await screen.findByRole("button", { name: "Cancel generation" })).toBeEnabled();
  });

  test.each(["downloading", "succeeded", "failed", "canceled"] as const)("%s has no Cancel button", async (status) => {
    const fake = installDefaultApi();
    const generation = status === "succeeded" ? succeededGeneration() : makeGeneration({ status });
    fake.on("GET", "/api/generations/gen_01TEST", { body: generation });
    renderApp("/create?gen=gen_01TEST");
    const card = await screen.findByTestId("result-status");
    await waitFor(() => {
      expect(card).not.toHaveTextContent("Loading…");
    });
    expect(screen.queryByRole("button", { name: "Cancel generation" })).not.toBeInTheDocument();
  });

  test("Cancel posts to the cancel endpoint and is disabled while pending", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: makeGeneration({ status: "running", stage: "plan" }) });
    let release: () => void = () => undefined;
    fake.on("POST", "/api/generations/gen_01TEST/cancel", () => new Promise((resolve) => {
      release = () => {
        fake.on("GET", "/api/generations/gen_01TEST", { body: makeGeneration({ status: "canceled" }) });
        resolve({ body: makeGeneration({ status: "canceled" }) });
      };
    }));
    renderApp("/create?gen=gen_01TEST");
    await userEvent.click(await screen.findByRole("button", { name: "Cancel generation" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Canceling…" })).toBeDisabled();
    });
    expect(fake.callsTo("POST", "/api/generations/gen_01TEST/cancel")).toHaveLength(1);
    release();
    expect(await screen.findByText("This generation was canceled.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
  });

  test("a running generation stays Canceling… until the poll says canceled", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fake = installDefaultApi();
    const running = makeGeneration({ status: "running", stage: "plan" });
    fake.on("GET", "/api/generations/gen_01TEST", { body: running });
    fake.on("POST", "/api/generations/gen_01TEST/cancel", { body: running });
    renderApp("/create?gen=gen_01TEST");
    await userEvent.click(await screen.findByRole("button", { name: "Cancel generation" }));
    expect(await screen.findByRole("button", { name: "Canceling…" })).toBeDisabled();

    // Polls that still say running (the worker has not reached a stage boundary).
    const polls = fake.callsTo("GET", "/api/generations/gen_01TEST").length;
    fake.on("GET", "/api/generations/gen_01TEST", { body: makeGeneration({ status: "running", stage: "semantic" }) });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(GENERATION_POLL_MS * 2);
    });
    expect(fake.callsTo("GET", "/api/generations/gen_01TEST").length).toBeGreaterThan(polls);
    expect(screen.getByRole("list", { name: "Stages" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Canceling…" })).toBeDisabled();
    expect(fake.callsTo("POST", "/api/generations/gen_01TEST/cancel")).toHaveLength(1);

    fake.on("GET", "/api/generations/gen_01TEST", { body: makeGeneration({ status: "canceled" }) });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(GENERATION_POLL_MS);
    });
    expect(await screen.findByText("This generation was canceled.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
  });

  test("a refused cancel shows the plain conflict message with Details", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: makeGeneration({ status: "running", stage: "save" }) });
    fake.on("POST", "/api/generations/gen_01TEST/cancel", {
      status: 409,
      body: envelope("conflict", "job is succeeded; it can no longer be canceled"),
    });
    renderApp("/create?gen=gen_01TEST");
    await userEvent.click(await screen.findByRole("button", { name: "Cancel generation" }));
    expect(
      await screen.findByText("That isn't possible while the generation is in its current state."),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("conflict")).toBeInTheDocument();
    expect(screen.getByText("job is succeeded; it can no longer be canceled")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel generation" })).toBeEnabled();
  });

  test("an unknown generation id shows not_found in plain language", async () => {
    installDefaultApi();
    renderApp("/create?gen=gen_GONE");
    expect(await screen.findByText("This could not be found. It may have been deleted.")).toBeInTheDocument();
  });
});
