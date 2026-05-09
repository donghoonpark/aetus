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
  await expect(page.locator(".fetch-status")).toBeVisible();
  await expect(page.getByText("fetching")).toBeHidden();
  await expect(page.locator("[data-testid='stream-chart'] canvas")).toBeVisible();
  await expect(page.getByText("env.temperature")).toBeHidden();
  await expect(page.getByText(/dense-device-1 \/ ch0 min/)).toBeHidden();
  await expect(page.getByText(/dense-device-1 \/ ch0 max/)).toBeHidden();

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
  await expect(page.getByRole("button", { name: /env.temperature 2 device · celsius scalar/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /env.humidity 2 device · percent scalar/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /motor.rpm 2 device · rpm scalar/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /pump.enabled 2 device · unitless scalar/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /machine.state 2 device · unitless scalar/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /dense.vibration 2 device · g sampled/ })).toBeVisible();
  await page.getByRole("button", { name: /env.temperature/ }).click();

  await expect(page.getByRole("heading", { name: "env.temperature" })).toBeVisible();
  await expect(page.getByText("20,000 plotted points")).toBeVisible();
  await expect(page.locator(".viewer-shell").getByText("scalar", { exact: true })).toBeVisible();
  expect(seriesRequests.some((url) => url.pathname.includes("env.temperature"))).toBeTruthy();
});

test("searches devices remotely from the control drawer", async ({ page }) => {
  const seriesRequests: URL[] = [];
  const deviceSearchRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests, { deviceSearchRequests });

  await page.goto("/");
  await page.getByTitle("Open controls").click();
  await page.getByTestId("device-search").click();
  await page.getByLabel("Search devices").fill("device-3");

  await expect
    .poll(() => deviceSearchRequests.some((url) => url.searchParams.get("search") === "device-3"))
    .toBe(true);
  await page.getByText("dense-device-3").click();

  await expect(page.getByText("3 devices")).toBeVisible();
  await expect.poll(() => seriesRequests.some((url) => url.pathname.includes("dense-device-3"))).toBe(true);
});

test("renders scalar streams across numeric, boolean, and string value types", async ({ page }) => {
  const seriesRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests);

  await page.goto("/");
  await page.getByTitle("Open controls").click();
  for (const [streamKey, expectedText] of [
    ["motor.rpm", "20,000 plotted points"],
    ["env.humidity", "20,000 plotted points"],
    ["pump.enabled", "20,000 plotted points"],
    ["machine.state", "20 plotted points"],
  ] as const) {
    await page.getByRole("button", { name: new RegExp(streamKey.replace(".", "\\.")) }).click();
    await expect(page.getByRole("heading", { name: streamKey })).toBeVisible();
    await expect(page.getByText(expectedText)).toBeVisible();
  }

  expect(seriesRequests.some((url) => url.pathname.includes("motor.rpm"))).toBeTruthy();
  expect(seriesRequests.some((url) => url.pathname.includes("env.humidity"))).toBeTruthy();
  expect(seriesRequests.some((url) => url.pathname.includes("pump.enabled"))).toBeTruthy();
  expect(seriesRequests.some((url) => url.pathname.includes("machine.state"))).toBeTruthy();
});

test("applies an explicit time range from the control drawer", async ({ page }) => {
  const seriesRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests);

  await page.goto("/");
  await page.getByTitle("Open controls").click();
  await expect(page.getByText("Time and density")).toBeVisible();
  const expectedRange = await page.evaluate(() => {
    const from = new Date("2026-05-03 00:40:00").toISOString();
    const to = new Date("2026-05-03 00:45:00").toISOString();
    return {
      from,
      label: `${from.slice(11, 19)} - ${to.slice(11, 19)}`,
    };
  });
  await page.getByPlaceholder("From").fill("2026-05-03 00:40:00");
  await page.getByPlaceholder("To").fill("2026-05-03 00:45:00");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Apply range" }).click();

  await expect(page.getByText(expectedRange.label)).toBeVisible();
  await expect
    .poll(() => seriesRequests.filter((url) => url.searchParams.get("from") === expectedRange.from).length)
    .toBe(2);
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

test("converts ECharts dataZoom percent payload into a server-side range fetch", async ({ page }) => {
  const seriesRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "dense.vibration" })).toBeVisible();
  await expect(page.getByText("40,000 plotted points")).toBeVisible();
  await page.evaluate(() => {
    window.dispatchEvent(
      new CustomEvent("aetus-test-datazoom", {
        detail: {
          batch: [{ start: 50, end: 60 }],
        },
      }),
    );
  });

  await expect
    .poll(() => seriesRequests.filter((url) => url.searchParams.get("from") === "2026-05-03T00:55:00.000Z").length)
    .toBe(2);
  await expect(page.getByText("00:55:00 - 00:56:00")).toBeVisible();
});

test("treats ECharts pan payloads as dynamic server-side range fetches", async ({ page }) => {
  const seriesRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "dense.vibration" })).toBeVisible();
  await expect(page.getByText("40,000 plotted points")).toBeVisible();
  await page.evaluate(() => {
    window.dispatchEvent(
      new CustomEvent("aetus-test-datazoom", {
        detail: {
          batch: [{ start: 20, end: 30 }],
        },
      }),
    );
  });

  await expect
    .poll(() => seriesRequests.filter((url) => url.searchParams.get("from") === "2026-05-03T00:52:00.000Z").length)
    .toBe(2);
  await expect(page.getByText("00:52:00 - 00:53:00")).toBeVisible();
});

test("pans the visible time range when the chart body is dragged", async ({ page }) => {
  const seriesRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "dense.vibration" })).toBeVisible();
  await expect(page.getByText("40,000 plotted points")).toBeVisible();
  await page.evaluate(() => {
    const chart = document.querySelector<HTMLElement>("[data-testid='stream-chart']");
    const deltaX = -(chart?.clientWidth ?? 1000) * 0.2;
    window.dispatchEvent(new CustomEvent("aetus-test-pan", { detail: { deltaX } }));
  });

  await expect(page.getByText("00:52:00 - 01:02:00")).toBeVisible();
  await expect
    .poll(() => seriesRequests.filter((url) => url.searchParams.get("from") === "2026-05-03T00:52:00.000Z").length)
    .toBe(2);
});

test("implements wheel zoom-out by expanding beyond the loaded chart extent", async ({ page }) => {
  const seriesRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "dense.vibration" })).toBeVisible();
  await expect(page.getByText("40,000 plotted points")).toBeVisible();
  await page.evaluate(() => {
    window.dispatchEvent(
      new CustomEvent("aetus-test-wheel-zoomout", {
        detail: { anchorRatio: 0.5 },
      }),
    );
  });

  await expect
    .poll(() => seriesRequests.filter((url) => url.searchParams.get("from") === "2026-05-03T00:46:00.000Z").length)
    .toBe(2);
  await expect(page.getByText("00:46:00 - 01:04:00")).toBeVisible();
});

test("uses the mouse anchor position for wheel zoom in and out", async ({ page }) => {
  const seriesRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "dense.vibration" })).toBeVisible();
  await expect(page.getByText("40,000 plotted points")).toBeVisible();
  await page.evaluate(() => {
    window.dispatchEvent(
      new CustomEvent("aetus-test-wheel-zoom", {
        detail: { anchorRatio: 0.25, direction: "in" },
      }),
    );
  });

  await expect(page.getByText("00:51:06 - 00:56:40")).toBeVisible();
  await expect
    .poll(() => seriesRequests.filter((url) => url.searchParams.get("from")?.startsWith("2026-05-03T00:51:06.66")).length)
    .toBe(2);

  await page.evaluate(() => {
    window.dispatchEvent(
      new CustomEvent("aetus-test-wheel-zoom", {
        detail: { anchorRatio: 0.75, direction: "out" },
      }),
    );
  });

  await expect(page.getByText("00:47:46 - 00:57:46")).toBeVisible();
  await expect
    .poll(() => seriesRequests.filter((url) => url.searchParams.get("from")?.startsWith("2026-05-03T00:47:46.66")).length)
    .toBe(2);
});

test("shows the expanded wheel zoom-out range before the server response arrives", async ({ page }) => {
  const seriesRequests: URL[] = [];
  let releaseZoomOutResponse: (() => void) | undefined;
  const zoomOutGate = new Promise<void>((resolve) => {
    releaseZoomOutResponse = resolve;
  });
  await mockQueryApi(page, seriesRequests, {
    holdZoomOutResponse: () => zoomOutGate,
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "dense.vibration" })).toBeVisible();
  await expect(page.getByText("40,000 plotted points")).toBeVisible();
  await page.evaluate(() => {
    window.dispatchEvent(
      new CustomEvent("aetus-test-wheel-zoomout", {
        detail: { anchorRatio: 0.5 },
      }),
    );
  });

  await expect(page.getByText("00:46:00 - 01:04:00")).toBeVisible();
  await expect(page.getByText("syncing")).toBeVisible();
  await expect
    .poll(() => seriesRequests.filter((url) => url.searchParams.get("from") === "2026-05-03T00:46:00.000Z").length)
    .toBe(2);
  releaseZoomOutResponse?.();
  await expect(page.getByText("synced")).toBeVisible();
});

test("debounces repeated wheel zoom-out events into a single range expansion", async ({ page }) => {
  const seriesRequests: URL[] = [];
  await mockQueryApi(page, seriesRequests);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "dense.vibration" })).toBeVisible();
  await expect(page.getByText("40,000 plotted points")).toBeVisible();
  await page.evaluate(() => {
    window.dispatchEvent(new CustomEvent("aetus-test-wheel-zoomout", { detail: { anchorRatio: 0.5 } }));
    window.dispatchEvent(new CustomEvent("aetus-test-wheel-zoomout", { detail: { anchorRatio: 0.5 } }));
    window.dispatchEvent(new CustomEvent("aetus-test-wheel-zoomout", { detail: { anchorRatio: 0.5 } }));
  });

  await expect(page.getByText("00:46:00 - 01:04:00")).toBeVisible();
  await expect
    .poll(() => seriesRequests.filter((url) => url.searchParams.get("from") === "2026-05-03T00:46:00.000Z").length)
    .toBe(2);
  expect(seriesRequests.some((url) => url.searchParams.get("from") === "2026-05-03T00:38:48.000Z")).toBe(false);
});

async function mockQueryApi(
  page: Page,
  seriesRequests: URL[],
  options: { holdZoomOutResponse?: () => Promise<void>; deviceSearchRequests?: URL[] } = {},
) {
  await page.route(/\/v1\/query\/devices(?:\?.*)?$/, async (route) => {
    expect(route.request().headers()["authorization"]).toBe("Bearer viewer-token");
    const url = new URL(route.request().url());
    options.deviceSearchRequests?.push(url);
    const search = url.searchParams.get("search") ?? "";
    const allDevices = ["dense-device-1", "dense-device-2", "dense-device-3", "query-device-9"];
    const devices = allDevices
      .filter((deviceId) => !search || deviceId.includes(search))
      .map((deviceId) => ({ device_id: deviceId }));
    await route.fulfill({ json: { devices } });
  });

  await page.route("**/v1/query/devices/*/streams", async (route) => {
    expect(route.request().headers()["authorization"]).toBe("Bearer viewer-token");
    const deviceId = route.request().url().match(/devices\/([^/]+)\/streams/)?.[1] ?? "unknown";
    await route.fulfill({
      json: {
        device_id: deviceId,
        streams: [
          {
            key: "env.temperature",
            kind: "scalar",
            unit: "celsius",
            value_type: "double",
            latest_event_time: "2026-05-03T01:00:00Z",
          },
          {
            key: "motor.rpm",
            kind: "scalar",
            unit: "rpm",
            value_type: "int",
            latest_event_time: "2026-05-03T01:00:00Z",
          },
          {
            key: "env.humidity",
            kind: "scalar",
            unit: "percent",
            value_type: "float",
            latest_event_time: "2026-05-03T01:00:00Z",
          },
          {
            key: "pump.enabled",
            kind: "scalar",
            unit: "unitless",
            value_type: "bool",
            latest_event_time: "2026-05-03T01:00:00Z",
          },
          {
            key: "machine.state",
            kind: "scalar",
            unit: "unitless",
            value_type: "string",
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
    if (url.searchParams.get("from") === "2026-05-03T00:46:00.000Z") {
      await options.holdZoomOutResponse?.();
    }
    if (["env.temperature", "env.humidity", "motor.rpm", "pump.enabled", "machine.state"].includes(streamKey)) {
      await route.fulfill({ json: scalarSeries(deviceId, streamKey, maxPoints) });
      return;
    }
    await route.fulfill({ json: sampledSeries(deviceId, streamKey, maxPoints) });
  });
}

function scalarSeries(deviceId: string, key: string, count: number) {
  if (key === "machine.state") {
    return {
      device_id: deviceId,
      key,
      kind: "scalar",
      value_type: "string",
      resolution: "raw",
      points: Array.from({ length: 10 }, (_, index) => ({
        ts: new Date(Date.UTC(2026, 4, 3, 0, 50, 0) + index * 60_000).toISOString(),
        text: index % 2 === 0 ? "warming" : "running",
      })),
    };
  }
  const valueType = key === "motor.rpm" ? "int" : key === "pump.enabled" ? "bool" : key === "env.humidity" ? "float" : "double";
  return {
    device_id: deviceId,
    key,
    kind: "scalar",
    value_type: valueType,
    resolution: "raw",
    points: Array.from({ length: count }, (_, index) => ({
      ts: new Date(Date.UTC(2026, 4, 3, 0, 50, 0) + index * 60).toISOString(),
      value: scalarValue(key, index),
      text: key === "pump.enabled" ? (index % 120 === 0 ? "false" : "true") : undefined,
    })),
  };
}

function scalarValue(key: string, index: number) {
  if (key === "motor.rpm") return 1700 + (index % 120);
  if (key === "env.humidity") return 45 + Math.sin(index / 90) * 5;
  if (key === "pump.enabled") return index % 120 === 0 ? 0 : 1;
  return 23 + Math.sin(index / 80);
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
        min: Math.sin(index / 30 + channelIndex) - 0.08,
        max: Math.sin(index / 30 + channelIndex) + 0.08,
      })),
    })),
  };
}
