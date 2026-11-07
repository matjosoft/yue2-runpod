import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { GENERATION_POLL_MS } from "../src/api/queries";
import { loadDraft, saveDraft } from "../src/components/draft";
import { EXAMPLE } from "../src/features/create/songForm";
import { envelope, installDefaultApi, makeGeneration, succeededGeneration, type FakeApi } from "./fakeApi";
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
    await screen.findByRole("link", { name: "Download FLAC" });
    const atTerminal = calls();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(GENERATION_POLL_MS * 3);
    });
    expect(calls()).toBe(atTerminal);
  });

  test("succeeded shows the player, Download FLAC and the other files (U-3)", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", { body: succeededGeneration() });
    const { container } = renderApp("/create?gen=gen_01TEST");
    const download = await screen.findByRole("link", { name: "Download FLAC" });
    expect(download).toHaveAttribute("href", "/api/generations/gen_01TEST/artifacts/audio.flac");
    const audio = container.querySelector("audio");
    expect(audio).toHaveAttribute("src", "/api/generations/gen_01TEST/artifacts/audio.flac");
    expect(audio).toHaveAttribute("controls");
    expect(screen.getByRole("link", { name: "score.abc" })).toHaveAttribute(
      "href",
      "/api/generations/gen_01TEST/artifacts/score.abc",
    );
    expect(screen.getByRole("link", { name: "result.json" })).toBeInTheDocument();
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
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

  test("an unknown generation id shows not_found in plain language", async () => {
    installDefaultApi();
    renderApp("/create?gen=gen_GONE");
    expect(await screen.findByText("This could not be found. It may have been deleted.")).toBeInTheDocument();
  });
});
