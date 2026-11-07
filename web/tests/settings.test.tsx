import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import type { WorkerCheckState } from "../src/api/client";
import { HEALTH_POLL_MS } from "../src/api/queries";
import { PILL } from "../src/components/StatusPill";
import { validateWorkerUrl } from "../src/features/settings/SettingsDialog";
import { envelope, health, installDefaultApi } from "./fakeApi";
import { renderApp } from "./render";

const EXPECTED: [WorkerCheckState, string, string][] = [
  ["not_configured", "grey", "Not configured"],
  ["unreachable", "red", "Unreachable"],
  ["unauthorized", "red", "Unauthorized"],
  ["incompatible", "red", "Incompatible"],
  ["error", "red", "Model error"],
  ["warming_up", "amber", "Warming up"],
  ["ready", "green", "Ready"],
];

describe("status pill (U-5)", () => {
  test("covers every worker check state", () => {
    expect(Object.keys(PILL).sort()).toEqual(EXPECTED.map(([s]) => s).sort());
  });

  test.each(EXPECTED)("%s is %s with label %s", async (state, tone, label) => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/health", { body: health(state) });
    renderApp("/library");
    const pill = await screen.findByTestId("status-pill");
    await waitFor(() => {
      expect(pill).toHaveAttribute("data-tone", tone);
    });
    expect(pill).toHaveTextContent(label);
  });

  test("polls /api/health every 10 s and follows a state change", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fake = installDefaultApi();
    fake.on("GET", "/api/health", { body: health("warming_up") });
    renderApp("/library");
    const pill = await screen.findByTestId("status-pill");
    await waitFor(() => {
      expect(pill).toHaveAttribute("data-tone", "amber");
    });
    expect(fake.callsTo("GET", "/api/health")).toHaveLength(1);
    fake.on("GET", "/api/health", { body: health("ready") });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(HEALTH_POLL_MS);
    });
    await waitFor(() => {
      expect(pill).toHaveAttribute("data-tone", "green");
    });
    expect(fake.callsTo("GET", "/api/health")).toHaveLength(2);
  });

  test("turns red when the app server stops answering after a good poll", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fake = installDefaultApi();
    renderApp("/library");
    const pill = await screen.findByTestId("status-pill");
    await waitFor(() => {
      expect(pill).toHaveAttribute("data-tone", "green");
    });
    fake.on("GET", "/api/health", () => Promise.reject(new TypeError("Failed to fetch")));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(HEALTH_POLL_MS);
    });
    await waitFor(() => {
      expect(pill).toHaveAttribute("data-tone", "red");
    });
    expect(pill).toHaveTextContent("App server unreachable");
  });

  test("is red when the app server cannot be reached", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/health", { status: 500, body: "boom" });
    renderApp("/library");
    await waitFor(() => {
      expect(screen.getByTestId("status-pill")).toHaveAttribute("data-tone", "red");
    });
  });
});

describe("worker URL rule", () => {
  test.each([
    ["https://abc-8000.proxy.runpod.net", null],
    ["http://localhost:8000", null],
    ["http://127.0.0.1:8000", null],
    ["http://[::1]:8000", null],
    ["", null],
  ])("accepts %s", (url, expected) => {
    expect(validateWorkerUrl(url)).toBe(expected);
  });

  test.each(["http://example.com", "ftp://localhost", "not a url"])("rejects %s", (url) => {
    expect(validateWorkerUrl(url)).not.toBeNull();
  });
});

describe("settings dialog (U-5)", () => {
  test("every control has a label; the token is a password field showing 'saved'", async () => {
    installDefaultApi();
    renderApp("/settings");
    expect(await screen.findByRole("dialog", { name: "Settings" })).toBeInTheDocument();
    const url = screen.getByLabelText("Worker URL");
    await waitFor(() => {
      expect(url).toHaveValue("http://127.0.0.1:8000");
    });
    const token = screen.getByLabelText("Token");
    expect(token).toHaveAttribute("type", "password");
    expect(token).toHaveValue("");
    expect(token).toHaveAttribute("placeholder", "saved");
    expect(screen.getByText(/A token is saved/)).toBeInTheDocument();
  });

  test("a blank token is left out of the PUT, so the saved one is kept", async () => {
    const fake = installDefaultApi();
    fake.on("PUT", "/api/settings", (req) => ({
      body: { worker_url: (req.body as { worker_url: string }).worker_url, token_set: true },
    }));
    renderApp("/settings");
    const url = await screen.findByLabelText("Worker URL");
    await waitFor(() => {
      expect(url).toHaveValue("http://127.0.0.1:8000");
    });
    await userEvent.clear(url);
    await userEvent.type(url, "https://pod-8000.proxy.runpod.net");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Settings saved.");
    expect(fake.callsTo("PUT", "/api/settings").map((c) => c.body)).toEqual([
      { worker_url: "https://pod-8000.proxy.runpod.net" },
    ]);
  });

  test("a typed token is sent, and an http URL to another host is refused", async () => {
    const fake = installDefaultApi();
    fake.on("PUT", "/api/settings", { body: { worker_url: "http://localhost:8000", token_set: true } });
    renderApp("/settings");
    const url = await screen.findByLabelText("Worker URL");
    await waitFor(() => {
      expect(url).toHaveValue("http://127.0.0.1:8000");
    });
    await userEvent.clear(url);
    await userEvent.type(url, "http://example.com");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Use https://, or http:// only for localhost.")).toBeInTheDocument();
    expect(fake.callsTo("PUT", "/api/settings")).toHaveLength(0);

    await userEvent.clear(url);
    await userEvent.type(url, "http://localhost:8000");
    await userEvent.type(screen.getByLabelText("Token"), "s3cret-token");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Settings saved.");
    expect(fake.callsTo("PUT", "/api/settings").map((c) => c.body)).toEqual([
      { worker_url: "http://localhost:8000", token: "s3cret-token" },
    ]);
    expect(screen.getByLabelText("Token")).toHaveValue("");
  });

  test("Test connection saves, checks, and shows 'rejected the token' (AT-4 wording)", async () => {
    const fake = installDefaultApi();
    fake.on("PUT", "/api/settings", { body: { worker_url: "http://127.0.0.1:8000", token_set: true } });
    fake.on("POST", "/api/worker/check", {
      body: { state: "unauthorized", error: envelope("unauthorized", "Missing or wrong token").error, worker: null },
    });
    renderApp("/settings");
    await waitFor(() => {
      expect(screen.getByLabelText("Worker URL")).toHaveValue("http://127.0.0.1:8000");
    });
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("The worker rejected the token.")).toBeInTheDocument();
    expect(fake.callsTo("PUT", "/api/settings")).toHaveLength(1);
    expect(fake.callsTo("POST", "/api/worker/check")).toHaveLength(1);
    // The pill takes the fresh check result at once.
    await waitFor(() => {
      expect(screen.getByTestId("status-pill")).toHaveAttribute("data-tone", "red");
    });
  });

  test("Test connection reports ready", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/health", { body: health("not_configured") });
    fake.on("PUT", "/api/settings", { body: { worker_url: "http://127.0.0.1:8000", token_set: true } });
    fake.on("POST", "/api/worker/check", { body: health("ready").worker });
    renderApp("/settings");
    await waitFor(() => {
      expect(screen.getByLabelText("Worker URL")).toHaveValue("http://127.0.0.1:8000");
    });
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Connected. The model is ready.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("status-pill")).toHaveAttribute("data-tone", "green");
    });
  });

  test("Save and Test stay disabled until the saved settings have loaded", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/settings", () => new Promise(() => undefined)); // never answers
    renderApp("/settings");
    await screen.findByRole("dialog", { name: "Settings" });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
  });

  test("a failed settings load is shown and nothing can be saved over it", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/settings", { status: 500, body: envelope("internal", "db locked") });
    renderApp("/settings");
    expect(await screen.findByText("Something unexpected went wrong. Try again.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Token"), "new-token");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(fake.callsTo("PUT", "/api/settings")).toHaveLength(0);
  });

  test("Save re-checks the worker so the pill follows at once", async () => {
    const fake = installDefaultApi();
    fake.on("PUT", "/api/settings", { body: { worker_url: "http://127.0.0.1:1", token_set: true } });
    fake.on("POST", "/api/worker/check", {
      body: { state: "unreachable", error: envelope("worker_unreachable", "refused").error, worker: null },
    });
    renderApp("/settings");
    const url = screen.getByLabelText("Worker URL");
    await waitFor(() => {
      expect(url).toHaveValue("http://127.0.0.1:8000");
    });
    expect(screen.getByTestId("status-pill")).toHaveAttribute("data-tone", "green");
    await userEvent.clear(url);
    await userEvent.type(url, "http://127.0.0.1:1");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(screen.getByTestId("status-pill")).toHaveAttribute("data-tone", "red");
    });
    expect(screen.getByText("Settings saved.")).toBeInTheDocument();
    expect(fake.callsTo("POST", "/api/worker/check")).toHaveLength(1);
  });

  test("Close leaves the dialog", async () => {
    installDefaultApi();
    renderApp("/settings");
    await userEvent.click(await screen.findByRole("button", { name: "Close" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
