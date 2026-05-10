<template>
  <n-config-provider>
    <section class="anomaly-panel">
      <header class="hero">
        <div>
          <p class="eyebrow">AETUS Anomaly Detection</p>
          <h1>Detection control panel</h1>
          <p class="hero-copy">Threshold jobs, recent events, and webhook delivery state in one embeddable Vue panel.</p>
        </div>
        <button class="refresh-button" type="button" :disabled="loading" @click="refresh">
          <RefreshCw :size="18" />
          Refresh
        </button>
      </header>

      <div class="status-grid">
        <article class="stat-card">
          <Activity :size="20" />
          <span>Jobs</span>
          <strong>{{ jobs.length }}</strong>
        </article>
        <article class="stat-card">
          <Siren :size="20" />
          <span>Open events</span>
          <strong>{{ openEventCount }}</strong>
        </article>
        <article class="stat-card">
          <RadioTower :size="20" />
          <span>Webhooks</span>
          <strong>{{ webhooks.length }}</strong>
        </article>
        <article class="stat-card" :class="{ stale: error }">
          <Wifi :size="20" />
          <span>API</span>
          <strong>{{ error ? "error" : "ready" }}</strong>
        </article>
      </div>

      <n-alert v-if="error" type="error" class="panel-alert" :show-icon="false">
        {{ error }}
      </n-alert>

      <div class="panel-layout">
        <aside class="job-card">
          <div class="section-title">
            <PlusCircle :size="18" />
            <h2>Create detector job</h2>
          </div>
          <form class="job-form" @submit.prevent="createJob">
            <label>
              Job key
              <input v-model.trim="draft.jobKey" required :placeholder="selectedDetector.exampleJobKey" />
            </label>
            <label>
              Device ID
              <input v-model.trim="draft.deviceId" required placeholder="esp32-device-001" />
            </label>
            <label>
              Stream key
              <input v-model.trim="draft.streamKey" required :placeholder="selectedDetector.exampleStream" />
            </label>
            <label>
              Channels
              <input v-model.trim="draft.channels" placeholder="accel_x, accel_y (optional)" />
            </label>
            <label>
              Detector
              <select v-model="draft.detectorType">
                <option v-for="detector in detectorOptions" :key="detector.type" :value="detector.type">
                  {{ detector.label }}
                </option>
              </select>
            </label>
            <p class="detector-help">{{ selectedDetector.description }}</p>
            <div class="form-row">
              <label>
                Operator
                <select v-model="draft.operator">
                  <option value="gt">greater than</option>
                  <option value="gte">greater or equal</option>
                  <option value="lt">less than</option>
                  <option value="lte">less or equal</option>
                </select>
              </label>
              <label>
                Threshold
                <input v-model.number="draft.threshold" type="number" step="0.001" />
              </label>
            </div>
            <div v-if="selectedDetector.fields.includes('range')" class="form-row">
              <label>
                Min
                <input v-model.number="draft.min" type="number" step="0.001" />
              </label>
              <label>
                Max
                <input v-model.number="draft.max" type="number" step="0.001" />
              </label>
            </div>
            <div v-if="selectedDetector.fields.includes('baseline')" class="form-row">
              <label>
                Baseline
                <input v-model.number="draft.baseline" type="number" step="0.001" />
              </label>
              <label>
                Tolerance
                <input v-model.number="draft.tolerance" type="number" step="0.001" />
              </label>
            </div>
            <div v-if="selectedDetector.fields.includes('ewma')" class="form-row">
              <label>
                EWMA alpha
                <input v-model.number="draft.alpha" type="number" min="0.001" max="1" step="0.001" />
              </label>
              <label>
                Baseline
                <input v-model.number="draft.baseline" type="number" step="0.001" />
              </label>
            </div>
            <div v-if="selectedDetector.fields.includes('stuck')" class="form-row">
              <label>
                Expected value
                <input v-model.number="draft.expectedValue" type="number" step="0.001" />
              </label>
              <label>
                Tolerance
                <input v-model.number="draft.tolerance" type="number" step="0.001" />
              </label>
            </div>
            <div v-if="selectedDetector.fields.includes('count')" class="form-row">
              <label>
                Min count
                <input v-model.number="draft.minCount" type="number" min="0" />
              </label>
              <label>
                Max count
                <input v-model.number="draft.maxCount" type="number" min="0" />
              </label>
            </div>
            <div v-if="selectedDetector.fields.includes('fft')" class="form-row">
              <label>
                Target frequency Hz
                <input v-model.number="draft.targetFrequencyHz" type="number" min="0" step="0.001" />
              </label>
              <label>
                FFT sample limit
                <input v-model.number="draft.fftSampleLimit" type="number" min="8" />
              </label>
            </div>
            <div class="form-row">
              <label>
                Window seconds
                <input v-model.number="draft.windowSeconds" required min="1" type="number" />
              </label>
              <label>
                Severity
                <select v-model="draft.severity">
                  <option value="info">info</option>
                  <option value="warning">warning</option>
                  <option value="critical">critical</option>
                </select>
              </label>
            </div>
            <label class="checkbox-row">
              <input v-model="draft.anchorEnabled" type="checkbox" />
              Use event anchor
            </label>
            <div v-if="draft.anchorEnabled" class="anchor-box">
              <label>
                Anchor stream
                <input v-model.trim="draft.anchorStream" placeholder="machine_state" />
              </label>
              <div class="form-row">
                <label>
                  String value
                  <input v-model.trim="draft.anchorValueString" placeholder="RUN" />
                </label>
                <label>
                  Bool value
                  <select v-model="draft.anchorValueBool">
                    <option value="">any</option>
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                </label>
              </div>
              <div class="form-row">
                <label>
                  Pre seconds
                  <input v-model.number="draft.anchorPreSeconds" type="number" min="0" />
                </label>
                <label>
                  Post seconds
                  <input v-model.number="draft.anchorPostSeconds" type="number" min="0" />
                </label>
              </div>
            </div>
            <button class="primary-button" type="submit" :disabled="creating">
              <ShieldCheck :size="18" />
              {{ creating ? "Creating..." : "Create job" }}
            </button>
          </form>
        </aside>

        <main class="tables">
          <section class="table-card">
            <div class="section-title">
              <Activity :size="18" />
              <h2>Detection jobs</h2>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Detector</th>
                    <th>Selector</th>
                    <th>Window</th>
                    <th>Severity</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="job in jobs" :key="job.job_id">
                    <td>{{ job.job_key }}</td>
                    <td>{{ job.detector_type }}</td>
                    <td>{{ selectorLabel(job) }}</td>
                    <td>{{ job.window_seconds }}s</td>
                    <td><span class="pill">{{ job.severity }}</span></td>
                  </tr>
                  <tr v-if="jobs.length === 0">
                    <td colspan="5">No detection jobs yet.</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="table-card">
            <div class="section-title">
              <Siren :size="18" />
              <h2>Recent events</h2>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Stream</th>
                    <th>Device</th>
                    <th>Score</th>
                    <th>Status</th>
                    <th>Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="event in events" :key="event.event_id">
                    <td>{{ event.stream_key }}</td>
                    <td>{{ event.device_id }}</td>
                    <td>{{ formatScore(event.score, event.threshold) }}</td>
                    <td><span class="pill event">{{ event.status }}</span></td>
                    <td>{{ formatTime(event.event_end) }}</td>
                  </tr>
                  <tr v-if="events.length === 0">
                    <td colspan="5">No anomaly events in the selected limit.</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="table-card">
            <div class="section-title">
              <RadioTower :size="18" />
              <h2>Webhook endpoints</h2>
            </div>
            <div class="webhook-list">
              <div v-for="webhook in webhooks" :key="webhook.endpoint_id" class="webhook-row">
                <span>{{ webhook.endpoint_key }}</span>
                <code>{{ webhook.url }}</code>
                <strong>{{ webhook.enabled ? "enabled" : "disabled" }}</strong>
              </div>
              <p v-if="webhooks.length === 0" class="empty-copy">No webhook endpoints configured.</p>
            </div>
          </section>
        </main>
      </div>
    </section>
  </n-config-provider>
</template>

<script setup lang="ts">
import { Activity, PlusCircle, RadioTower, RefreshCw, ShieldCheck, Siren, Wifi } from "lucide-vue-next";
import { NAlert, NConfigProvider } from "naive-ui";
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";

interface JobResponse {
  job_id: number;
  job_key: string;
  enabled: boolean;
  device_selector: { devices?: string[] };
  stream_selector: { streams?: string[] };
  detector_type: string;
  detector_config: Record<string, unknown>;
  window_seconds: number;
  step_seconds: number;
  lookback_seconds: number;
  severity: string;
}

interface EventResponse {
  event_id: string;
  job_id: number;
  device_id: string;
  stream_key: string;
  channel_key?: string | null;
  event_end: string;
  severity: string;
  status: string;
  score: number;
  threshold?: number | null;
}

interface WebhookEndpointResponse {
  endpoint_id: number;
  endpoint_key: string;
  enabled: boolean;
  url: string;
}

const props = withDefaults(
  defineProps<{
    anomalyServerUrl: string;
    authToken?: string;
    autoRefreshMs?: number;
  }>(),
  {
    authToken: "",
    autoRefreshMs: 0,
  },
);

const jobs = ref<JobResponse[]>([]);
const events = ref<EventResponse[]>([]);
const webhooks = ref<WebhookEndpointResponse[]>([]);
const loading = ref(false);
const creating = ref(false);
const error = ref("");
let intervalId: number | undefined;

const detectorOptions = [
  {
    type: "threshold",
    label: "Threshold",
    description: "Simple upper or lower bound for scalar metrics and decoded signal channels.",
    fields: [] as string[],
    exampleJobKey: "temperature-high",
    exampleStream: "temperature",
  },
  {
    type: "range",
    label: "Range",
    description: "Flags values outside a normal min/max operating range.",
    fields: ["range"],
    exampleJobKey: "pressure-out-of-range",
    exampleStream: "pressure",
  },
  {
    type: "mean_threshold",
    label: "Mean threshold",
    description: "Detects sustained high or low average over the selected window.",
    fields: [] as string[],
    exampleJobKey: "current-mean-high",
    exampleStream: "motor.current",
  },
  {
    type: "rms_threshold",
    label: "RMS threshold",
    description: "Useful for vibration, current waveform energy, and dense signal frames.",
    fields: [] as string[],
    exampleJobKey: "vibration-rms-high",
    exampleStream: "motor.vibration",
  },
  {
    type: "peak_abs_threshold",
    label: "Peak abs threshold",
    description: "Detects shock, spikes, and transient high absolute values.",
    fields: [] as string[],
    exampleJobKey: "imu-shock",
    exampleStream: "imu.accel",
  },
  {
    type: "stddev_threshold",
    label: "Stddev threshold",
    description: "Detects unstable, noisy, or highly variable windows.",
    fields: [] as string[],
    exampleJobKey: "noise-high",
    exampleStream: "sensor.noise",
  },
  {
    type: "delta_threshold",
    label: "Delta threshold",
    description: "Uses absolute difference between the first and last window values.",
    fields: [] as string[],
    exampleJobKey: "tank-level-jump",
    exampleStream: "tank.level",
  },
  {
    type: "rate_of_change",
    label: "Rate of change",
    description: "Time-normalized change rate for fast rise/fall detection.",
    fields: [] as string[],
    exampleJobKey: "temperature-rise-fast",
    exampleStream: "temperature",
  },
  {
    type: "zscore_threshold",
    label: "Z-score",
    description: "Detects outliers relative to a device-specific baseline and sigma.",
    fields: ["baseline"],
    exampleJobKey: "current-zscore",
    exampleStream: "motor.current",
  },
  {
    type: "ewma_deviation",
    label: "EWMA deviation",
    description: "Tracks drift and sudden deviation from an exponential moving average.",
    fields: ["ewma"],
    exampleJobKey: "pressure-drift",
    exampleStream: "pressure",
  },
  {
    type: "missing_data",
    label: "Missing data",
    description: "Flags windows with too few samples or events.",
    fields: ["count"],
    exampleJobKey: "heartbeat-missing",
    exampleStream: "heartbeat",
  },
  {
    type: "flatline",
    label: "Flatline",
    description: "Detects values that barely move across enough samples.",
    fields: ["count"],
    exampleJobKey: "sensor-flatline",
    exampleStream: "adc.raw",
  },
  {
    type: "stuck_at",
    label: "Stuck at",
    description: "Detects values stuck near an expected saturation or error value.",
    fields: ["stuck", "count"],
    exampleJobKey: "adc-saturated",
    exampleStream: "adc.raw",
  },
  {
    type: "duty_cycle",
    label: "Duty cycle",
    description: "Measures ON ratio for bool or numeric state streams.",
    fields: ["baseline"],
    exampleJobKey: "pump-duty-high",
    exampleStream: "pump.enabled",
  },
  {
    type: "event_sequence",
    label: "Event sequence",
    description: "Checks whether an anchored window has too few or too many events.",
    fields: ["count"],
    exampleJobKey: "state-event-count",
    exampleStream: "machine_state",
  },
  {
    type: "fft_threshold",
    label: "FFT threshold",
    description: "Naive DFT magnitude check for target or dominant vibration frequencies.",
    fields: ["fft"],
    exampleJobKey: "motor-frequency-energy",
    exampleStream: "motor.vibration",
  },
];

const draft = reactive({
  jobKey: "temperature-high",
  deviceId: "python-client-e2e",
  streamKey: "temperature",
  channels: "",
  detectorType: "threshold",
  operator: "gt",
  threshold: 50,
  min: undefined as number | undefined,
  max: undefined as number | undefined,
  baseline: undefined as number | undefined,
  tolerance: undefined as number | undefined,
  alpha: undefined as number | undefined,
  expectedValue: undefined as number | undefined,
  minCount: undefined as number | undefined,
  maxCount: undefined as number | undefined,
  targetFrequencyHz: undefined as number | undefined,
  fftSampleLimit: 2048,
  windowSeconds: 60,
  severity: "warning",
  anchorEnabled: false,
  anchorStream: "",
  anchorValueString: "",
  anchorValueBool: "",
  anchorPreSeconds: 0,
  anchorPostSeconds: 10,
});

const openEventCount = computed(() => events.value.filter((event) => event.status === "open").length);
const selectedDetector = computed(
  () => detectorOptions.find((detector) => detector.type === draft.detectorType) ?? detectorOptions[0],
);

function headers() {
  return props.authToken ? { "x-aetus-admin-token": props.authToken } : {};
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${props.anomalyServerUrl.replace(/\/$/, "")}${path}`, {
    ...init,
    headers: {
      ...headers(),
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

async function refresh() {
  loading.value = true;
  error.value = "";
  try {
    const [nextJobs, nextEvents, nextWebhooks] = await Promise.all([
      request<JobResponse[]>("/v1/anomaly/jobs"),
      request<EventResponse[]>("/v1/anomaly/events?limit=50"),
      request<WebhookEndpointResponse[]>("/v1/anomaly/webhooks/endpoints"),
    ]);
    jobs.value = nextJobs;
    events.value = nextEvents;
    webhooks.value = nextWebhooks;
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc);
  } finally {
    loading.value = false;
  }
}

async function createJob() {
  creating.value = true;
  error.value = "";
  try {
    const detectorConfig = compactConfig({
      operator: draft.operator,
      threshold: draft.threshold,
      min: draft.min,
      max: draft.max,
      baseline: draft.baseline,
      tolerance: draft.tolerance,
      alpha: draft.alpha,
      expected_value: draft.expectedValue,
      min_count: draft.minCount,
      max_count: draft.maxCount,
      target_frequency_hz: draft.targetFrequencyHz,
      fft_sample_limit: selectedDetector.value.fields.includes("fft") ? draft.fftSampleLimit : undefined,
      anchor: draft.anchorEnabled
        ? compactConfig({
            stream: draft.anchorStream,
            value_string: draft.anchorValueString || undefined,
            value_bool:
              draft.anchorValueBool === "true" ? true : draft.anchorValueBool === "false" ? false : undefined,
            pre_seconds: draft.anchorPreSeconds,
            post_seconds: draft.anchorPostSeconds,
            max_events: 1,
          })
        : undefined,
    });
    const channels = draft.channels
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    await request<JobResponse>("/v1/anomaly/jobs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        job_key: draft.jobKey,
        enabled: true,
        device_selector: { devices: [draft.deviceId] },
        stream_selector: compactConfig({ streams: [draft.streamKey], channels: channels.length ? channels : undefined }),
        detector_type: draft.detectorType,
        detector_config: detectorConfig,
        window_seconds: draft.windowSeconds,
        step_seconds: draft.windowSeconds,
        severity: draft.severity,
      }),
    });
    await refresh();
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc);
  } finally {
    creating.value = false;
  }
}

function compactConfig<T extends Record<string, unknown>>(source: T) {
  return Object.fromEntries(
    Object.entries(source).filter(([, value]) => value !== undefined && value !== null && value !== ""),
  );
}

function selectorLabel(job: JobResponse) {
  const device = job.device_selector.devices?.[0] ?? "*";
  const stream = job.stream_selector.streams?.[0] ?? "*";
  return `${device} / ${stream}`;
}

function formatScore(score: number, threshold?: number | null) {
  const scoreText = Number.isFinite(score) ? score.toFixed(3).replace(/\.?0+$/, "") : "-";
  if (threshold === undefined || threshold === null) {
    return scoreText;
  }
  return `${scoreText} / ${threshold}`;
}

function formatTime(value: string) {
  return new Date(value).toLocaleString();
}

function restartAutoRefresh() {
  if (intervalId !== undefined) {
    window.clearInterval(intervalId);
    intervalId = undefined;
  }
  if (props.autoRefreshMs > 0) {
    intervalId = window.setInterval(refresh, props.autoRefreshMs);
  }
}

onMounted(async () => {
  await refresh();
  restartAutoRefresh();
});

onUnmounted(() => {
  if (intervalId !== undefined) {
    window.clearInterval(intervalId);
  }
});

watch(() => props.autoRefreshMs, restartAutoRefresh);
</script>

<style scoped>
.anomaly-panel {
  min-height: 100vh;
  padding: 32px;
  color: #172033;
  background:
    radial-gradient(circle at top left, rgba(96, 165, 250, 0.28), transparent 34rem),
    linear-gradient(135deg, #f8fbff 0%, #eef6ff 48%, #f7f2ff 100%);
  font-family:
    Avenir Next,
    ui-sans-serif,
    system-ui,
    sans-serif;
}

.hero,
.table-card,
.job-card,
.stat-card {
  border: 1px solid rgba(92, 113, 145, 0.18);
  box-shadow: 0 24px 60px rgba(46, 64, 100, 0.12);
  background: rgba(255, 255, 255, 0.82);
  backdrop-filter: blur(18px);
}

.hero {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: center;
  padding: 24px;
  border-radius: 28px;
}

.eyebrow {
  margin: 0 0 8px;
  color: #5b5fd7;
  font-size: 0.76rem;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

h1,
h2,
p {
  margin: 0;
}

h1 {
  font-size: clamp(2rem, 4vw, 4rem);
  letter-spacing: -0.06em;
}

h2 {
  font-size: 1rem;
}

.hero-copy {
  margin-top: 8px;
  color: #5f6d83;
}

button {
  cursor: pointer;
}

.refresh-button,
.primary-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  border: 0;
  border-radius: 16px;
  min-height: 44px;
  padding: 0 18px;
  color: white;
  font-weight: 800;
  background: linear-gradient(135deg, #5b5fd7, #35a7ff);
}

.refresh-button:disabled,
.primary-button:disabled {
  opacity: 0.55;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin: 18px 0;
}

.stat-card {
  display: grid;
  gap: 4px;
  padding: 18px;
  border-radius: 22px;
}

.stat-card svg {
  color: #5571e8;
}

.stat-card span {
  color: #66758d;
  font-size: 0.84rem;
}

.stat-card strong {
  font-size: 1.6rem;
}

.stat-card.stale strong {
  color: #d94b4b;
}

.panel-alert {
  margin-bottom: 16px;
}

.panel-layout {
  display: grid;
  grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
  gap: 18px;
}

.job-card,
.table-card {
  border-radius: 26px;
  padding: 20px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.section-title svg {
  color: #5b5fd7;
}

.job-form,
.tables {
  display: grid;
  gap: 16px;
}

.detector-help {
  border-radius: 16px;
  padding: 12px;
  color: #52627a;
  background: rgba(91, 157, 255, 0.1);
  font-size: 0.84rem;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

label {
  display: grid;
  gap: 6px;
  color: #647086;
  font-size: 0.82rem;
  font-weight: 800;
}

.checkbox-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.checkbox-row input {
  width: auto;
}

.anchor-box {
  display: grid;
  gap: 12px;
  border: 1px solid #dce8f7;
  border-radius: 18px;
  padding: 14px;
  background: rgba(247, 251, 255, 0.86);
}

input,
select {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #d9e4f2;
  border-radius: 14px;
  padding: 11px 12px;
  color: #172033;
  background: white;
  outline: none;
}

input:focus,
select:focus {
  border-color: #5b9dff;
  box-shadow: 0 0 0 4px rgba(91, 157, 255, 0.14);
}

.table-wrap {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  padding: 12px 10px;
  border-bottom: 1px solid #edf2f7;
  text-align: left;
  white-space: nowrap;
}

th {
  color: #687a93;
  font-size: 0.76rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.pill {
  display: inline-flex;
  border-radius: 999px;
  padding: 4px 10px;
  color: #3653bd;
  background: #ecf1ff;
  font-size: 0.78rem;
  font-weight: 800;
}

.pill.event {
  color: #b04c00;
  background: #fff3df;
}

.webhook-list {
  display: grid;
  gap: 10px;
}

.webhook-row {
  display: grid;
  grid-template-columns: minmax(120px, 180px) minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 12px;
  border-radius: 16px;
  background: rgba(241, 246, 255, 0.85);
}

code {
  overflow: hidden;
  color: #53627a;
  text-overflow: ellipsis;
}

.empty-copy {
  color: #6f7a8e;
}

@media (max-width: 980px) {
  .anomaly-panel {
    padding: 18px;
  }

  .hero,
  .panel-layout {
    grid-template-columns: 1fr;
  }

  .hero {
    display: grid;
  }

  .status-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .status-grid,
  .form-row {
    grid-template-columns: 1fr;
  }
}
</style>
