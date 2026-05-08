import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const now = new Date("2026-05-03T01:00:00Z");

test.beforeEach(async ({ page }) => {
  await page.addInitScript((fixedNow) => {
    const RealDate = Date;
    class MockDate extends RealDate {
      constructor(...args: ConstructorParameters<DateConstructor>) {
        if (args.length === 0) {
          super(fixedNow);
          return;
        }
        super(...args);
      }
      static now() {
        return new RealDate(fixedNow).getTime();
      }
    }
    Object.setPrototypeOf(MockDate, RealDate);
    window.Date = MockDate as DateConstructor;
  }, now.toISOString());
});

test("renders a hidden-control multi-device sampled panel with bounded dense fetches", async ({ page }) => {
  const seriesRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests);

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "dense.vibration" })).toBeVisible();
  await expect(page.getByText("2 devices")).toBeVisible();
  await expect(page.getByText("40,000 plotted points")).toBeVisible();
  await expect(page.locator("[data-testid='stream-chart'] canvas")).toBeVisible();
  await expect(page.getByText("dense.temperature")).toBeHidden();

  const visibleRequests = seriesRequests.filter((url) => url.searchParams.get("from") === "2026-05-03T00:50:00.000Z");
  expect(visibleRequests).toHaveLength(2);
  for (const url of visibleRequests) {
    expect(url.searchParams.get("max_points")).toBe("10000");
  }
});

test("opens controls, switches stream, and keeps JWT on query requests", async ({ page }) => {
  const seriesRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests);

  await page.goto("/");
  await page.getByTitle("Open controls").click();
  await expect(page.getByText("Panel controls")).toBeVisible();
  await page.getByRole("button", { name: /dense.temperature/ }).click();

  await expect(page.getByRole("heading", { name: "dense.temperature" })).toBeVisible();
  await expect(page.getByText("20,000 plotted points")).toBeVisible();
  expect(seriesRequests.some((url) => url.pathname.includes("dense.temperature"))).toBeTruthy();
});

test("refetches high density data when the visible zoom range changes", async ({ page }) => {
  const seriesRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "dense.vibration" })).toBeVisible();
  await expect(page.getByText("40,000 plotted points")).toBeVisible();
  await page.evaluate(() => {
    window.dispatchEvent(
      new CustomEvent("aetus-test-zoom", {
        detail: {
          from: "2026-05-03T00:55:00.000Z",
          to: "2026-05-03T00:56:00.000Z",
        },
      }),
    );
  });

  await expect.poll(() => seriesRequests.filter((url) => url.searchParams.get("from") === "2026-05-03T00:55:00.000Z").length).toBe(2);
  await expect(page.getByText("00:55:00 - 00:56:00")).toBeVisible();
});

async function mockQueryApi(page: Page, seriesRequests: URL[]) {
  await page.route("**/v1/query/devices/*/streams", async (route) => {
    expect(route.request().headers()["authorization"]).toBe("Bearer viewer-token");
    const deviceId = route.request().url().match(/devices\/([^/]+)\/streams/)?.[1] ?? "unknown";
    await route.fulfill({
      json: {
        device_id: deviceId,
        streams: [
          {
            key: "dense.temperature",
            kind: "scalar",
            unit: "celsius",
            latest_event_time: "2026-05-03T01:00:00Z",
          },
          {
            key: "dense.vibration",
            kind: "sampled",
            unit: "g",
            nominal_rate_hz: 277.8,
            encoding: "float32_le",
            layout: "interleaved",
            channels: [
              { key: "accel_x", unit: "g" },
              { key: "accel_y", unit: "g" },
            ],
            latest_event_time: "2026-05-03T01:00:00Z",
          },
        ],
      },
    });
  });

  await page.route(/\/v1\/query\/devices\/[^/]+\/streams\/[^/]+\/series.*/, async (route) => {
    expect(route.request().headers()["authorization"]).toBe("Bearer viewer-token");
    const url = new URL(route.request().url());
    seriesRequests.push(url);
    const [, deviceId, streamKey] = url.pathname.match(/devices\/([^/]+)\/streams\/([^/]+)\/series/) ?? [];
    const maxPoints = Number(url.searchParams.get("max_points") ?? "10000");
    if (streamKey === "dense.temperature") {
      await route.fulfill({ json: scalarSeries(deviceId, streamKey, maxPoints) });
      return;
    }
    await route.fulfill({ json: sampledSeries(deviceId, streamKey, maxPoints) });
  });
}

function scalarSeries(deviceId: string, key: string, count: number) {
  return {
    device_id: deviceId,
    key,
    kind: "scalar",
    resolution: "raw",
    points: Array.from({ length: count }, (_, index) => ({
      ts: new Date(Date.UTC(2026, 4, 3, 0, 50, 0) + index * 60).toISOString(),
      value: 23 + Math.sin(index / 80),
    })),
  };
}

function sampledSeries(deviceId: string, key: string, count: number) {
  return {
    device_id: deviceId,
    key,
    kind: "sampled",
    resolution: "62.5ms",
    mode: "samples",
    channels: ["accel_x", "accel_y"].map((name, channelIndex) => ({
      name,
      unit: "g",
      points: Array.from({ length: count }, (_, index) => ({
        ts: new Date(Date.UTC(2026, 4, 3, 0, 50, 0) + index * 60).toISOString(),
        value: Math.sin(index / 30 + channelIndex),
      })),
    })),
  };
}
