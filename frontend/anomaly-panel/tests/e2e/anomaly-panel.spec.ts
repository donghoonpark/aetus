import { expect, test } from "@playwright/test";
import type { Page, Request } from "@playwright/test";

test("renders anomaly jobs, events, webhook endpoints, and authenticates requests", async ({ page }) => {
  const requests: Request[] = [];
  await mockAnomalyApi(page, requests);

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Detection control panel" })).toBeVisible();
  await expect(page.getByText("temperature-high")).toBeVisible();
  await expect(page.getByText("python-client-e2e / temperature")).toBeVisible();
  await expect(page.getByRole("cell", { name: "temperature", exact: true })).toBeVisible();
  await expect(page.getByText("22.75 / 20")).toBeVisible();
  await expect(page.getByText("ops-webhook")).toBeVisible();

  expect(requests.every((request) => request.headers()["x-aetus-admin-token"] === "e2e-anomaly-admin-token")).toBeTruthy();
});

test("creates a threshold job and refreshes the table", async ({ page }) => {
  const requests: Request[] = [];
  await mockAnomalyApi(page, requests);

  await page.goto("/");
  await page.getByLabel("Job key").fill("rpm-high");
  await page.getByLabel("Device ID").fill("motor-001");
  await page.getByLabel("Stream key").fill("motor.rpm");
  await page.getByLabel("Threshold").fill("2800");
  await page.getByRole("button", { name: "Create job" }).click();

  await expect(page.getByText("rpm-high")).toBeVisible();
  const createRequest = requests.find((request) => request.method() === "POST" && request.url().endsWith("/v1/anomaly/jobs"));
  expect(createRequest).toBeDefined();
  const body = createRequest ? JSON.parse(createRequest.postData() ?? "{}") : {};
  expect(body.detector_config).toEqual({ operator: "gt", threshold: 2800 });
  expect(body.device_selector).toEqual({ devices: ["motor-001"] });
  expect(body.stream_selector).toEqual({ streams: ["motor.rpm"] });
});

async function mockAnomalyApi(page: Page, requests: Request[]) {
  let jobs = [
    {
      job_id: 1,
      job_key: "temperature-high",
      enabled: true,
      device_selector: { devices: ["python-client-e2e"] },
      stream_selector: { streams: ["temperature"] },
      detector_type: "threshold",
      detector_config: { operator: "gt", threshold: 20 },
      window_seconds: 60,
      step_seconds: 60,
      lookback_seconds: 0,
      severity: "warning",
      created_at: "2026-05-10T00:00:00Z",
      updated_at: "2026-05-10T00:00:00Z",
    },
  ];
  await page.route("**/v1/anomaly/**", async (route) => {
    const request = route.request();
    requests.push(request);
    const url = new URL(request.url());
    if (url.pathname === "/v1/anomaly/jobs" && request.method() === "GET") {
      await route.fulfill({ json: jobs });
      return;
    }
    if (url.pathname === "/v1/anomaly/jobs" && request.method() === "POST") {
      const body = JSON.parse(request.postData() ?? "{}");
      const created = {
        ...body,
        job_id: 2,
        created_at: "2026-05-10T00:01:00Z",
        updated_at: "2026-05-10T00:01:00Z",
      };
      jobs = [...jobs, created];
      await route.fulfill({ json: created });
      return;
    }
    if (url.pathname === "/v1/anomaly/events") {
      await route.fulfill({
        json: [
          {
            event_id: "5b55297b-41a4-4b77-a82a-b3508ed96b64",
            job_id: 1,
            device_id: "python-client-e2e",
            stream_key: "temperature",
            channel_key: null,
            event_end: "2026-05-10T00:02:00Z",
            severity: "warning",
            status: "open",
            score: 22.75,
            threshold: 20,
          },
        ],
      });
      return;
    }
    if (url.pathname === "/v1/anomaly/webhooks/endpoints") {
      await route.fulfill({
        json: [
          {
            endpoint_id: 1,
            endpoint_key: "ops-webhook",
            enabled: true,
            url: "https://example.invalid/anomaly",
          },
        ],
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not found" });
  });
}
