import { defineConfig, devices } from "@playwright/test";
import { STUDIO_PORT, STUDIO_URL, WORKER_KEY, WORKER_PORT } from "./e2e/env";

// One end-to-end run against `make run-fake` (PRD section 12): a fake-engine worker and
// the local server serving the freshly built UI. No GPU is involved.
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  reporter: [["list"]],
  use: {
    baseURL: STUDIO_URL,
    trace: "retain-on-failure",
    launchOptions: { args: ["--autoplay-policy=no-user-gesture-required"] },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "pnpm build && bash ../scripts/run_fake.sh",
    url: `${STUDIO_URL}/api/health`,
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: "ignore",
    stderr: "pipe",
    gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
    env: {
      WORKER_PORT: WORKER_PORT.toString(),
      STUDIO_PORT: STUDIO_PORT.toString(),
      WORKER_API_KEY: WORKER_KEY,
      FAKE_STAGE_DELAY_S: "2", // about 10 s per song: long enough to watch and reload
      // Only warnings and errors from both services reach the test output.
      LOG_LEVEL: "WARNING",
      STUDIO_LOG_LEVEL: "WARNING",
    },
  },
});
