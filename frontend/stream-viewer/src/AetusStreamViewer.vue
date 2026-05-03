<template>
  <n-config-provider>
    <section class="aetus-stream-viewer">
        <header class="viewer-header">
          <div>
            <p class="eyebrow">AETUS Stream Viewer</p>
            <h1>{{ selectedStream?.key ?? "Streams" }}</h1>
          </div>
          <n-space align="center" :size="10">
            <n-tag v-if="selectedStream" :type="selectedStream.kind === 'sampled' ? 'info' : 'success'" round>
              {{ selectedStream.kind }}
            </n-tag>
            <n-button secondary circle title="Refresh" :loading="loading" @click="refresh">
              <n-icon :component="RefreshCw" />
            </n-button>
          </n-space>
        </header>

        <n-grid cols="1 l:12" responsive="screen" :x-gap="16" :y-gap="16">
          <n-grid-item :span="3">
            <n-card embedded class="panel-card" content-style="padding: 12px">
              <n-space vertical :size="12">
                <n-input
                  v-model:value="deviceIdModel"
                  placeholder="Device ID"
                  clearable
                  @keyup.enter="loadStreams"
                />
                <n-button type="primary" block :loading="loadingStreams" @click="loadStreams">
                  Load Streams
                </n-button>
                <n-divider style="margin: 2px 0" />
                <n-empty v-if="streams.length === 0 && !loadingStreams" description="No streams" size="small" />
                <button
                  v-for="stream in streams"
                  :key="stream.key"
                  class="stream-row"
                  :class="{ active: stream.key === selectedKey }"
                  type="button"
                  @click="selectStream(stream.key)"
                >
                  <span>
                    <strong>{{ stream.key }}</strong>
                    <small>{{ stream.unit ?? "unitless" }}</small>
                  </span>
                  <n-tag size="small" :type="stream.kind === 'sampled' ? 'info' : 'success'">
                    {{ stream.kind }}
                  </n-tag>
                </button>
              </n-space>
            </n-card>
          </n-grid-item>

          <n-grid-item :span="9">
            <n-space vertical :size="16">
              <n-card embedded class="panel-card" content-style="padding: 12px">
                <n-flex justify="space-between" align="center" wrap>
                  <n-space align="center">
                    <n-select
                      v-model:value="rangePreset"
                      :options="rangeOptions"
                      style="width: 140px"
                      @update:value="applyRangePreset"
                    />
                    <n-input-number
                      v-model:value="maxPoints"
                      :min="100"
                      :max="10000"
                      :step="100"
                      style="width: 130px"
                    />
                  </n-space>
                  <n-space align="center">
                    <n-text depth="3">{{ seriesPointCount }} plotted points</n-text>
                    <n-button secondary :loading="loadingSeries" @click="loadSeries">
                      Update
                    </n-button>
                  </n-space>
                </n-flex>
              </n-card>

              <n-card embedded class="panel-card chart-card" content-style="padding: 0">
                <div ref="chartEl" class="chart-surface" data-testid="stream-chart"></div>
              </n-card>

              <n-grid cols="1 m:3" responsive="screen" :x-gap="12" :y-gap="12">
                <n-grid-item v-for="item in summaryCards" :key="item.label">
                  <n-card embedded size="small" class="metric-card">
                    <n-text depth="3">{{ item.label }}</n-text>
                    <strong>{{ item.value }}</strong>
                  </n-card>
                </n-grid-item>
              </n-grid>
            </n-space>
          </n-grid-item>
        </n-grid>
    </section>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent } from "echarts/components";
import { LineChart } from "echarts/charts";
import { CanvasRenderer } from "echarts/renderers";
import { RefreshCw } from "lucide-vue-next";
import {
  NButton,
  NCard,
  NConfigProvider,
  NDivider,
  NEmpty,
  NFlex,
  NGrid,
  NGridItem,
  NIcon,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NTag,
  NText,
  createDiscreteApi,
} from "naive-ui";

echarts.use([GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, LineChart, CanvasRenderer]);

type StreamKind = "scalar" | "sampled";

interface StreamInfo {
  key: string;
  kind: StreamKind;
  unit: string | null;
  latest_event_time: string;
  channels?: Array<{ key: string; unit?: string | null }>;
  nominal_rate_hz?: number | null;
}

interface SeriesPoint {
  ts: string;
  value?: number;
  min?: number;
  max?: number;
  avg?: number | null;
}

interface SeriesResponse {
  device_id: string;
  key: string;
  kind: StreamKind;
  resolution: string;
  mode?: string;
  points?: SeriesPoint[];
  channels?: Array<{ name: string; unit?: string | null; points: SeriesPoint[] }>;
}

const props = withDefaults(
  defineProps<{
    queryServerUrl: string;
    deviceId?: string;
    initialStreamKey?: string;
    maxPointsPerRequest?: number;
  }>(),
  {
    deviceId: "",
    initialStreamKey: "",
    maxPointsPerRequest: 1500,
  },
);

const { message } = createDiscreteApi(["message"]);
const chartEl = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;

const normalizedQueryUrl = computed(() => props.queryServerUrl.replace(/\/$/, ""));
const deviceIdModel = ref(props.deviceId);
const streams = ref<StreamInfo[]>([]);
const selectedKey = ref(props.initialStreamKey);
const series = ref<SeriesResponse | null>(null);
const maxPoints = ref(props.maxPointsPerRequest);
const rangePreset = ref("1h");
const loadingStreams = ref(false);
const loadingSeries = ref(false);
const loading = computed(() => loadingStreams.value || loadingSeries.value);

const rangeOptions = [
  { label: "10 min", value: "10m" },
  { label: "1 hour", value: "1h" },
  { label: "6 hours", value: "6h" },
  { label: "1 day", value: "1d" },
];

const selectedStream = computed(() => streams.value.find((stream) => stream.key === selectedKey.value) ?? null);
const seriesPointCount = computed(() => {
  if (!series.value) return 0;
  if (series.value.kind === "scalar") return series.value.points?.length ?? 0;
  return series.value.channels?.reduce((total, channel) => total + channel.points.length, 0) ?? 0;
});
const summaryCards = computed(() => [
  { label: "Device", value: deviceIdModel.value || "-" },
  { label: "Resolution", value: series.value?.resolution ?? "-" },
  { label: "Rate", value: selectedStream.value?.nominal_rate_hz ? `${selectedStream.value.nominal_rate_hz.toFixed(1)} Hz` : "-" },
]);

onMounted(async () => {
  await loadStreams();
  window.addEventListener("resize", resizeChart);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", resizeChart);
  chart?.dispose();
  chart = null;
});

watch(selectedKey, () => {
  void loadSeries();
});

async function refresh() {
  await loadStreams();
  await loadSeries();
}

async function loadStreams() {
  if (!deviceIdModel.value) return;
  loadingStreams.value = true;
  try {
    const response = await fetch(`${normalizedQueryUrl.value}/v1/query/devices/${encodeURIComponent(deviceIdModel.value)}/streams`);
    if (!response.ok) throw new Error(`streams request failed: ${response.status}`);
    const body = (await response.json()) as { streams: StreamInfo[] };
    streams.value = body.streams;
    if (!selectedKey.value || !streams.value.some((stream) => stream.key === selectedKey.value)) {
      selectedKey.value = streams.value[0]?.key ?? "";
    }
    await loadSeries();
  } catch (error) {
    message.error(error instanceof Error ? error.message : "Failed to load streams");
  } finally {
    loadingStreams.value = false;
  }
}

async function loadSeries() {
  if (!deviceIdModel.value || !selectedKey.value) return;
  loadingSeries.value = true;
  try {
    const { from, to } = currentRange();
    const params = new URLSearchParams({ from, to, max_points: String(maxPoints.value) });
    const response = await fetch(
      `${normalizedQueryUrl.value}/v1/query/devices/${encodeURIComponent(deviceIdModel.value)}/streams/${encodeURIComponent(selectedKey.value)}/series?${params}`,
    );
    if (!response.ok) throw new Error(`series request failed: ${response.status}`);
    series.value = (await response.json()) as SeriesResponse;
    await nextTick();
    renderChart();
  } catch (error) {
    message.error(error instanceof Error ? error.message : "Failed to load series");
  } finally {
    loadingSeries.value = false;
  }
}

function selectStream(key: string) {
  selectedKey.value = key;
}

function applyRangePreset() {
  void loadSeries();
}

function currentRange() {
  const to = new Date();
  const from = new Date(to.getTime() - rangeToMs(rangePreset.value));
  return { from: from.toISOString(), to: to.toISOString() };
}

function rangeToMs(value: string) {
  if (value === "10m") return 10 * 60 * 1000;
  if (value === "6h") return 6 * 60 * 60 * 1000;
  if (value === "1d") return 24 * 60 * 60 * 1000;
  return 60 * 60 * 1000;
}

function renderChart() {
  if (!chartEl.value || !series.value) return;
  chart ??= echarts.init(chartEl.value);
  const chartSeries =
    series.value.kind === "scalar"
      ? [
          {
            name: series.value.key,
            type: "line",
            showSymbol: false,
            data: (series.value.points ?? []).map((point) => [point.ts, point.value]),
          },
        ]
      : (series.value.channels ?? []).flatMap((channel) => [
          {
            name: `${channel.name} min`,
            type: "line",
            showSymbol: false,
            data: channel.points.map((point) => [point.ts, point.min]),
          },
          {
            name: `${channel.name} max`,
            type: "line",
            showSymbol: false,
            data: channel.points.map((point) => [point.ts, point.max]),
          },
        ]);

  chart.setOption({
    animation: false,
    color: ["#2563eb", "#06b6d4", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed"],
    tooltip: { trigger: "axis" },
    legend: { top: 8, type: "scroll" },
    grid: { left: 48, right: 24, top: 54, bottom: 54 },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 22, bottom: 14 }],
    xAxis: { type: "time" },
    yAxis: { type: "value", scale: true },
    series: chartSeries,
  });
}

function resizeChart() {
  chart?.resize();
}
</script>

<style scoped>
.aetus-stream-viewer {
  min-height: 100vh;
  padding: 18px;
  color: #18202f;
  background:
    linear-gradient(135deg, rgba(231, 245, 255, 0.9), rgba(244, 247, 251, 0.95)),
    repeating-linear-gradient(90deg, rgba(27, 45, 78, 0.04) 0 1px, transparent 1px 40px);
}

.viewer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 2px 18px;
}

.viewer-header h1 {
  margin: 2px 0 0;
  font-size: clamp(24px, 4vw, 36px);
  line-height: 1.05;
}

.eyebrow {
  margin: 0;
  color: #5b6472;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}

.panel-card {
  border-radius: 8px;
}

.stream-row {
  width: 100%;
  border: 1px solid #dde5ef;
  border-radius: 8px;
  padding: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  background: #fff;
  cursor: pointer;
  text-align: left;
}

.stream-row.active {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.12);
}

.stream-row strong,
.stream-row small {
  display: block;
}

.stream-row small {
  margin-top: 2px;
  color: #667085;
}

.chart-card {
  overflow: hidden;
}

.chart-surface {
  width: 100%;
  height: min(58vh, 520px);
  min-height: 360px;
}

.metric-card strong {
  display: block;
  margin-top: 4px;
  font-size: 20px;
}
</style>
