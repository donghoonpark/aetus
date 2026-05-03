import { expect, test } from "@playwright/test";

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

  await page.route("http://127.0.0.1:18001/v1/query/devices/query-device-1/streams", async (route) => {
    await route.fulfill({
      json: {
        device_id: "query-device-1",
        streams: [
          {
            key: "temperature",
            kind: "scalar",
            unit: "celsius",
            latest_event_time: "2026-05-03T01:00:00Z",
          },
          {
            key: "imu.accel",
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

  await page.route(/\/v1\/query\/devices\/query-device-1\/streams\/imu\.accel\/series.*/, async (route) => {
    await route.fulfill({
      json: {
        device_id: "query-device-1",
        key: "imu.accel",
        kind: "sampled",
        resolution: "4s",
        mode: "envelope",
        channels: [
          {
            name: "accel_x",
            unit: "g",
            points: [
              { ts: "2026-05-03T00:00:00Z", min: -0.2, max: 0.3, avg: 0.01 },
              { ts: "2026-05-03T00:00:04Z", min: -0.4, max: 0.5, avg: 0.02 },
            ],
          },
          {
            name: "accel_y",
            unit: "g",
            points: [
              { ts: "2026-05-03T00:00:00Z", min: -0.1, max: 0.2, avg: 0.01 },
              { ts: "2026-05-03T00:00:04Z", min: -0.3, max: 0.4, avg: 0.02 },
            ],
          },
        ],
      },
    });
  });

  await page.route(/\/v1\/query\/devices\/query-device-1\/streams\/temperature\/series.*/, async (route) => {
    await route.fulfill({
      json: {
        device_id: "query-device-1",
        key: "temperature",
        kind: "scalar",
        resolution: "raw",
        points: [
          { ts: "2026-05-03T00:00:00Z", value: 23.5 },
          { ts: "2026-05-03T00:10:00Z", value: 23.7 },
        ],
      },
    });
  });
});

test("renders sampled stream chart from query server URL", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "imu.accel" })).toBeVisible();
  await expect(page.locator("header").getByText("sampled")).toBeVisible();
  await expect(page.getByText("4 plotted points")).toBeVisible();
  await expect(page.locator("[data-testid='stream-chart'] canvas")).toBeVisible();
});

test("switches to scalar stream without changing component props", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /temperature/ }).click();

  await expect(page.getByRole("heading", { name: "temperature" })).toBeVisible();
  await expect(page.locator("header").getByText("scalar")).toBeVisible();
  await expect(page.getByText("2 plotted points")).toBeVisible();
});
