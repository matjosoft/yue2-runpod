import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import { installDefaultApi, makeGeneration, SCORE_TEXT, succeededGeneration } from "./fakeApi";
import { renderApp } from "./render";

const LONG_STYLE = "English, jazz-funk, warm lead vocal, Rhodes, bass and drums, with brass stabs and a slow groove";

describe("Library (U-4)", () => {
  test("rows are newest first and show title, style preview, status, time and a player", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations", {
      body: {
        items: [
          succeededGeneration({ id: "gen_02", title: "Newer", request: { style: LONG_STYLE, lyrics: "x", seed: 1 } }),
          makeGeneration({ id: "gen_01", title: "Older", status: "running", stage: "plan" }),
        ],
        next_cursor: null,
      },
    });
    renderApp("/library");
    const rows = await screen.findAllByTestId("library-row");
    expect(rows.map((r) => within(r).getAllByRole("link")[0]?.textContent)).toEqual(["Newer", "Older"]);
    const first = rows[0] as HTMLElement;
    expect(within(first).getByText(`${LONG_STYLE.slice(0, 80)}…`)).toBeInTheDocument();
    expect(within(first).getByText("succeeded")).toBeInTheDocument();
    expect(first.querySelector("time")).toHaveAttribute("datetime", "2026-11-07T10:00:00Z");
    expect(first.querySelector("audio")).toHaveAttribute("src", "/api/generations/gen_02/artifacts/audio.flac");
    // A generation in progress has no player, and must be canceled before it can be deleted.
    const second = rows[1] as HTMLElement;
    expect(second.querySelector("audio")).toBeNull();
    expect(within(second).getByRole("button", { name: "Delete Older" })).toBeDisabled();
    expect(within(second).getByRole("button", { name: "Cancel Older" })).toBeEnabled();
    expect(within(first).queryByRole("button", { name: "Cancel Newer" })).not.toBeInTheDocument();
  });

  test("Cancel on an in-progress row posts and refreshes the list", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations", {
      body: { items: [makeGeneration({ title: "Busy", status: "queued" })], next_cursor: null },
    });
    fake.on("POST", "/api/generations/gen_01TEST/cancel", () => {
      const canceled = makeGeneration({ title: "Busy", status: "canceled" });
      fake.on("GET", "/api/generations", { body: { items: [canceled], next_cursor: null } });
      return { body: canceled };
    });
    renderApp("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Cancel Busy" }));
    expect(await screen.findByText("canceled")).toBeInTheDocument();
    expect(fake.callsTo("POST", "/api/generations/gen_01TEST/cancel")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Cancel Busy" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Busy" })).toBeEnabled();
  });

  test("a style of 80 characters or fewer is shown whole", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations", {
      body: { items: [succeededGeneration({ request: { style: "short style", lyrics: "x" } })], next_cursor: null },
    });
    renderApp("/library");
    expect(await screen.findByText("short style")).toBeInTheDocument();
  });

  test("Delete asks for confirmation; cancelling sends nothing", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations", { body: { items: [succeededGeneration({ title: "Song" })], next_cursor: null } });
    renderApp("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Delete Song" }));
    expect(screen.getByText("Delete “Song” and its files?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(fake.callsTo("DELETE", /^\/api\/generations\//)).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Delete Song" })).toBeInTheDocument();
  });

  test("confirming deletes and refreshes the list", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations", { body: { items: [succeededGeneration({ title: "Song" })], next_cursor: null } });
    fake.on("DELETE", "/api/generations/gen_01TEST", () => {
      fake.on("GET", "/api/generations", { body: { items: [], next_cursor: null } });
      return { status: 204 };
    });
    renderApp("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Delete Song" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByText(/No songs yet/)).toBeInTheDocument();
    expect(fake.callsTo("DELETE", "/api/generations/gen_01TEST")).toHaveLength(1);
  });

  test("Reuse settings opens Create filled with the stored request, seed included", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations", {
      body: {
        items: [succeededGeneration({ request: { style: "disco", lyrics: "[Chorus]\nhey", seed: 31337, cot: "off" } })],
        next_cursor: null,
      },
    });
    renderApp("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Reuse settings" }));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent(/^\/create$/);
    });
    expect(await screen.findByLabelText("Style")).toHaveValue("disco");
    expect(screen.getByLabelText("Lyrics")).toHaveValue("[Chorus]\nhey");
    expect(screen.getByLabelText("Seed")).toHaveValue("31337");
    expect(screen.getByLabelText("Planning mode")).toHaveValue("off");
  });

  test("the detail view shows the score, and Edit score opens Create with it", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations/gen_01TEST", {
      body: succeededGeneration({ title: "Scored", request: { style: "waltz", lyrics: "[Verse]\none", seed: 9, cot: "full" } }),
    });
    renderApp("/library/gen_01TEST");
    expect(await screen.findByTestId("score-text")).toHaveTextContent("CDEF GABc");
    await userEvent.click(screen.getByRole("button", { name: "Edit score" }));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent(/^\/create$/);
    });
    expect(await screen.findByLabelText("Score (ABC)")).toHaveValue(SCORE_TEXT);
    expect(screen.getByLabelText("Style")).toHaveValue("waltz");
    expect(screen.getByLabelText("Seed")).toHaveValue("9");
  });

  test("the empty state links to Create", async () => {
    installDefaultApi();
    renderApp("/library");
    expect(await screen.findByText(/No songs yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create your first one" })).toHaveAttribute("href", "/create");
  });

  test("Load more fetches the next page with the cursor", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations", (req) =>
      req.url.searchParams.get("cursor") === "gen_02"
        ? { body: { items: [succeededGeneration({ id: "gen_01", title: "Page two" })], next_cursor: null } }
        : { body: { items: [succeededGeneration({ id: "gen_02", title: "Page one" })], next_cursor: "gen_02" } },
    );
    renderApp("/library");
    await screen.findByText("Page one");
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Page two")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  test("a row opens the detail view with request, artifacts with sizes, and timings", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/generations", { body: { items: [succeededGeneration({ title: "Detail me" })], next_cursor: null } });
    fake.on("GET", "/api/generations/gen_01TEST", { body: succeededGeneration({ title: "Detail me" }) });
    renderApp("/library");
    await userEvent.click(await screen.findByRole("link", { name: "Detail me" }));
    expect(await screen.findByRole("heading", { name: "Detail me" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("Request")).getByText("seed")).toBeInTheDocument();
    expect(within(screen.getByLabelText("Request")).getByText("12345")).toBeInTheDocument();
    expect(screen.getAllByText("30.0 MB").length).toBeGreaterThan(0);
    expect(within(screen.getByLabelText("Timings")).getByText("plan_s")).toBeInTheDocument();
    expect(within(screen.getByLabelText("Timings")).getByText("1.2 s")).toBeInTheDocument();
  });
});
