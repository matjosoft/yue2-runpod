import { expect, test, type Page } from "@playwright/test";
import { WORKER_KEY, WORKER_URL } from "./env";

// The tests share one server and run in order; the last one breaks the worker settings.
test.describe.configure({ mode: "serial" });

/** Sample rate and channel count from a FLAC file's STREAMINFO block. */
function flacInfo(bytes: Uint8Array): { sampleRate: number; channels: number } {
  const magic = String.fromCharCode(...bytes.slice(0, 4));
  if (magic !== "fLaC") throw new Error(`not a FLAC file: ${magic}`);
  if ((bytes[4] ?? 0xff) & 0x7f) throw new Error("first metadata block is not STREAMINFO");
  const b = (i: number) => bytes[i] ?? 0;
  return {
    sampleRate: (b(18) << 12) | (b(19) << 4) | (b(20) >> 4),
    channels: ((b(20) >> 1) & 0x07) + 1,
  };
}

async function closeSettings(page: Page) {
  await page.getByRole("button", { name: "Close" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
}

test("configure, create, watch progress, reload, play, list, delete (AT-1)", async ({ page, request }) => {
  // Configure the worker in Settings.
  await page.goto("/settings");
  await page.getByLabel("Worker URL").fill(WORKER_URL);
  await page.getByLabel("Token").fill(WORKER_KEY);
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByText("Connected. The model is ready.")).toBeVisible();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Settings saved.")).toBeVisible();
  await expect(page.getByLabel("Token")).toHaveAttribute("placeholder", "saved");
  await expect(page.getByTestId("status-pill")).toHaveAttribute("data-tone", "green");
  await closeSettings(page);
  await expect(page).toHaveURL(/\/create$/);

  // Create from the example.
  await page.getByRole("button", { name: "Example" }).click();
  await page.getByLabel("Title").fill("E2E song");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/\/create\?gen=gen_/);
  const id = new URL(page.url()).searchParams.get("gen") ?? "";

  // Watch progress: the stage list follows the operation's declared stages.
  const stages = page.getByRole("list", { name: "Stages" });
  await expect(stages).toBeVisible({ timeout: 15_000 });
  await expect(stages.locator('[aria-current="step"]')).toHaveCount(1);
  const generation = (await (await request.get(`/api/generations/${id}`)).json()) as { stages: string[] };
  const order = generation.stages;
  const labels = (await stages.getByRole("listitem").allTextContents()).map((t) => t.slice(1));
  expect(labels).toEqual(order);

  // The index of the stage marked current, or -1 when no stage list is shown.
  const currentIndex = async () => {
    const marked = stages.locator('[aria-current="step"]');
    if ((await marked.count()) === 0) return -1;
    return order.indexOf(((await marked.first().textContent()) ?? "").slice(1));
  };
  const seen: number[] = [await currentIndex()];
  expect(seen[0]).toBeGreaterThanOrEqual(0);

  // Reload mid-run: the URL keeps the generation and the live progress comes back (U-2).
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`gen=${id}`));
  await expect(stages).toBeVisible({ timeout: 5_000 });
  await expect(stages.locator('[aria-current="step"]')).toHaveCount(1);

  // Follow the run to the end: the current stage only moves forward, in declared order.
  const download = page.getByRole("link", { name: "Download FLAC" });
  const deadline = Date.now() + 45_000;
  while (!(await download.isVisible()) && Date.now() < deadline) {
    const index = await currentIndex();
    if (index >= 0 && index !== seen[seen.length - 1]) seen.push(index);
    await page.waitForTimeout(250);
  }
  await expect(download).toBeVisible();
  expect(seen).toEqual([...seen].sort((a, b) => a - b));
  expect(new Set(seen).size).toBeGreaterThanOrEqual(3);
  await expect(page.getByLabel("Title")).toHaveValue("E2E song"); // the draft survived too

  // The stored FLAC plays in the browser and is 48 kHz stereo.
  const audio = page.getByRole("region", { name: "Result" }).locator("audio");
  await audio.evaluate((el) => (el as HTMLAudioElement).play());
  await expect
    .poll(() =>
      audio.evaluate((el) => {
        const a = el as HTMLAudioElement;
        return a.error === null && a.duration > 0 && a.currentTime > 0;
      }),
    )
    .toBe(true);
  const href = (await download.getAttribute("href")) ?? "";
  const flac = await request.get(href);
  expect(flac.status()).toBe(200);
  expect(flacInfo(new Uint8Array(await flac.body()))).toEqual({ sampleRate: 48000, channels: 2 });

  // The library lists it; delete it with confirmation and its files are gone.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Library" }).click();
  const row = page.getByTestId("library-row").filter({ hasText: "E2E song" });
  await expect(row).toHaveCount(1);
  await expect(row.getByText("succeeded")).toBeVisible();
  await row.getByRole("button", { name: "Delete E2E song" }).click();
  await row.getByRole("button", { name: "Confirm delete" }).click();
  await expect(row).toHaveCount(0);
  expect((await request.get(href)).status()).toBe(404);
  expect((await request.get(`/api/generations/${id}`)).status()).toBe(404);
});

test("empty fields are blocked; an over-limit style shows the API's field message (AT-2)", async ({ page }) => {
  await page.goto("/create");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText("Style is required.")).toBeVisible();
  await expect(page.getByText("Lyrics are required.")).toBeVisible();

  const longStyle = "x".repeat(1001);
  await page.getByLabel("Style").fill(longStyle);
  await page.getByLabel("Lyrics").fill("[Verse]\nla la");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText(/1000 characters/)).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Some fields are not valid.");
  await expect(page).toHaveURL(/\/create$/);
  await expect(page.getByLabel("Style")).toHaveValue(longStyle);
});

test("an unreachable worker turns the pill red and Create keeps the input (AT-3)", async ({ page }) => {
  await page.goto("/settings");
  await page.getByLabel("Worker URL").fill("http://127.0.0.1:1");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Settings saved.")).toBeVisible();
  await closeSettings(page);

  // Server check every 5 s plus the pill's 10 s poll: allow up to about 15 s.
  await expect(page.getByTestId("status-pill")).toHaveAttribute("data-tone", "red", { timeout: 20_000 });
  await expect(page.getByTestId("status-pill")).toContainText("Unreachable");

  await page.getByLabel("Title").fill("Kept title");
  await page.getByLabel("Style").fill("kept style");
  await page.getByLabel("Lyrics").fill("[Chorus]\nkept lyrics");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText("Can't reach the GPU worker. Is the pod running?")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByLabel("Title")).toHaveValue("Kept title");
  await expect(page.getByLabel("Style")).toHaveValue("kept style");
  await expect(page.getByLabel("Lyrics")).toHaveValue("[Chorus]\nkept lyrics");
  await expect(page).toHaveURL(/\/create$/);
});
