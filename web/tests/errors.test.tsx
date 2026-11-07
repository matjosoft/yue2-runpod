import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import serverOpenapi from "../../contracts/openapi/server.json";
import { ApiError, api, call } from "../src/api/client";
import { ErrorMessage } from "../src/components/ErrorMessage";
import { ERROR_MESSAGES, plainMessage, requestFieldErrors } from "../src/components/errorMessages";
import { envelope, installFakeApi } from "./fakeApi";

const schemas = serverOpenapi.components.schemas as Record<string, { enum?: string[] }>;

describe("error messages (U-6)", () => {
  test("every error code in the server contract has a plain-language message", () => {
    const codes = [...(schemas["ErrorCode"]?.enum ?? []), ...(schemas["ClientErrorCode"]?.enum ?? [])];
    expect(codes.length).toBeGreaterThanOrEqual(14);
    for (const code of codes) {
      expect(ERROR_MESSAGES, code).toHaveProperty(code);
      expect((ERROR_MESSAGES as Record<string, string>)[code]).not.toBe("");
    }
  });

  test("uses the PRD wording", () => {
    expect(plainMessage({ code: "worker_unreachable" })).toBe(
      "Can't reach the GPU worker. Is the pod running?",
    );
    expect(plainMessage({ code: "unauthorized" })).toBe("The worker rejected the token.");
  });

  test("validation_error shows the field-level messages from details", () => {
    const info = envelope("validation_error", "invalid", {
      fields: [
        { loc: "request.style", msg: "String should have at most 1000 characters" },
        { loc: "title", msg: "too long" },
      ],
    }).error;
    expect(plainMessage(info)).toBe("style: String should have at most 1000 characters\ntitle: too long");
    expect(requestFieldErrors(info)).toEqual({
      style: "String should have at most 1000 characters",
      title: "too long",
    });
  });

  test("an unknown code falls back to a generic message", () => {
    expect(plainMessage({ code: "brand_new" as "internal" })).toBe(
      "Something unexpected went wrong. Try again.",
    );
  });

  test("the Details toggle shows the code and raw message", async () => {
    render(<ErrorMessage error={envelope("oom", "CUDA out of memory").error} />);
    expect(screen.getByText("The GPU ran out of memory. Try shorter lyrics, or retry.")).toBeInTheDocument();
    expect(screen.queryByText("CUDA out of memory")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("oom")).toBeInTheDocument();
    expect(screen.getByText("CUDA out of memory")).toBeInTheDocument();
  });
});

describe("api client", () => {
  test("turns an error envelope into an ApiError", async () => {
    const fake = installFakeApi();
    fake.on("GET", "/api/health", { status: 502, body: envelope("worker_unreachable", "connect failed") });
    const err = await call(api.GET("/api/health")).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).info.code).toBe("worker_unreachable");
  });

  test("a network failure becomes server_unreachable", async () => {
    installFakeApi();
    globalThis.fetch = () => Promise.reject(new TypeError("Failed to fetch"));
    const err = await call(api.GET("/api/health")).catch((e: unknown) => e);
    expect((err as ApiError).info.code).toBe("server_unreachable");
  });

  test("returns data on success", async () => {
    const fake = installFakeApi();
    fake.on("GET", "/api/settings", { body: { worker_url: null, token_set: false } });
    await expect(call(api.GET("/api/settings"))).resolves.toEqual({ worker_url: null, token_set: false });
  });
});
