import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { buildRoutes, navItems, visibleFeatures } from "../src/app/routes";
import { fallbackView, operationViews, viewFor, type OperationViewProps } from "../src/components/operationViews";
import { FEATURES } from "../src/features";
import type { FeatureModule } from "../src/features/types";
import { capabilities, envelope, installDefaultApi, makeGeneration, succeededGeneration } from "./fakeApi";
import { renderApp } from "./render";

// A throwaway module and operation that exist only in this test file (PRD section 7).
const echoFeature: FeatureModule = {
  id: "echo",
  requires: ["test.echo"],
  routes: [{ path: "echo", element: <h1>Echo page</h1> }],
  nav: { label: "Echo", to: "/echo", order: 5 },
};

describe("feature registry", () => {
  test("the MVP registers create, library and settings", () => {
    expect(FEATURES.map((f) => f.id)).toEqual(["create", "library", "settings"]);
  });

  test("nav comes from the modules, sorted by order", () => {
    expect(navItems([echoFeature, ...FEATURES]).map((n) => n.label)).toEqual([
      "Create",
      "Library",
      "Settings",
      "Echo",
    ]);
  });

  test("routes come from the modules, under the shell", () => {
    const [root] = buildRoutes([...FEATURES, echoFeature]);
    const paths = (root?.children ?? []).map((r) => r.path);
    expect(paths).toEqual(expect.arrayContaining(["create", "library", "library/:id", "settings", "echo"]));
  });

  test("a module is hidden only when loaded capabilities lack its operation", () => {
    const all = [...FEATURES, echoFeature];
    expect(visibleFeatures(all, null).map((f) => f.id)).toContain("echo");
    expect(visibleFeatures(all, new Set(["song.generate"])).map((f) => f.id)).toEqual([
      "create",
      "library",
      "settings",
    ]);
    expect(visibleFeatures(all, new Set(["test.echo"])).map((f) => f.id)).toEqual([
      "library",
      "settings",
      "echo",
    ]);
  });
});

describe("shell", () => {
  test("/ redirects to Create and the nav shows the modules", async () => {
    installDefaultApi();
    renderApp("/");
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/create");
    });
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Create" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Library" })).toBeInTheDocument();
    expect(screen.getByText(/CC BY-NC 4.0/)).toBeInTheDocument();
  });

  test("hides a module whose operation the worker lacks", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/capabilities", { body: capabilities(["song.generate"]) });
    renderApp("/echo", { features: [...FEATURES, echoFeature] });
    expect(await screen.findByText("This page isn't available.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Echo" })).not.toBeInTheDocument();
  });

  test("shows a module when the worker offers its operation", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/capabilities", { body: capabilities(["song.generate", "test.echo"]) });
    renderApp("/echo", { features: [...FEATURES, echoFeature] });
    expect(await screen.findByRole("heading", { name: "Echo page" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Echo" })).toBeInTheDocument();
  });

  test("shows every module while capabilities are unavailable", async () => {
    const fake = installDefaultApi();
    fake.on("GET", "/api/capabilities", { status: 409, body: envelope("not_configured", "no worker") });
    renderApp("/echo", { features: [...FEATURES, echoFeature] });
    await waitFor(() => {
      expect(fake.callsTo("GET", "/api/capabilities")).toHaveLength(1);
    });
    expect(screen.getByRole("heading", { name: "Echo page" })).toBeInTheDocument();
  });
});

describe("operationViews", () => {
  afterEach(() => {
    delete operationViews["test.echo"];
  });

  test("the MVP registers song.generate only", () => {
    expect(Object.keys(operationViews)).toEqual(["song.generate"]);
  });

  test("a view registered for a new operation renders through the registry", () => {
    operationViews["test.echo"] = {
      Summary: ({ generation }: OperationViewProps) => <span>echo says {String(generation.request["text"])}</span>,
      Result: () => <span>echo result</span>,
    };
    const { Summary } = viewFor("test.echo");
    render(<Summary generation={makeGeneration({ operation: "test.echo", request: { text: "hi" } })} />);
    expect(screen.getByText("echo says hi")).toBeInTheDocument();
  });

  test("an unknown operation uses the fallback view", () => {
    expect(viewFor("nope.nothing")).toBe(fallbackView);
    const { Result } = viewFor("nope.nothing");
    render(<Result generation={succeededGeneration({ operation: "nope.nothing" })} />);
    expect(screen.getByRole("link", { name: "audio.flac" })).toBeInTheDocument();
  });
});
