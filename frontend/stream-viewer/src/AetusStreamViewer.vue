<template>
  <n-config-provider>
    <section class="aetus-stream-viewer">
      <header class="viewer-shell">
        <div class="title-block">
          <p class="eyebrow">{{ panelTitle }}</p>
          <h1>{{ selectedTitle }}</h1>
          <p class="subline" data-testid="visible-range">
            {{ selectedDeviceIds.length }} device{{ selectedDeviceIds.length === 1 ? "" : "s" }}
            · {{ plottedPointCount.toLocaleString() }} plotted points
            · {{ visibleRangeLabel }}
          </p>
        </div>
        <n-space align="center" :size="10">
          <n-tag v-if="selectedKind" :type="selectedKind === 'sampled' ? 'info' : selectedKind === 'mixed' ? 'warning' : 'success'" round>
            {{ selectedKind }}
          </n-tag>
          <n-button secondary circle title="Refresh" @click="refresh">
            <n-icon :component="RefreshCw" />
          </n-button>
          <n-button type="primary" secondary circle title="Open controls" @click="drawerOpen = true">
            <n-icon :component="SlidersHorizontal" />
          </n-button>
        </n-space>
      </header>

      <main class="chart-panel">
        <div v-if="selectedKeys.length === 0" class="empty-state">
          <n-empty description="Select a device and stream from the control drawer" />
          <n-button type="primary" @click="drawerOpen = true">Open controls</n-button>
        </div>
        <div ref="chartEl" class="chart-surface" data-testid="stream-chart"></div>
        <div class="floating-status">
          <span class="fetch-status" :class="{ active: loading }">
            <i></i>
            {{ loading ? "syncing" : "synced" }}
          </span>
          <span>{{ seriesResolution }}</span>
          <span>{{ selectedChannels.length ? selectedChannels.join(", ") : "all channels" }}</span>
        </div>
      </main>

      <n-drawer v-model:show="drawerOpen" placement="right" :width="drawerWidth">
        <n-drawer-content title="Panel controls" closable>
          <n-space vertical :size="18">
            <n-card embedded size="small" title="Devices">
              <n-space vertical :size="10">
                <n-select
                  v-model:value="selectedDeviceIds"
                  data-testid="device-search"
                  class="device-select"
                  multiple
                  filterable
                  remote
                  clearable
                  :max-tag-count="1"
                  :input-props="{ 'aria-label': 'Search devices' }"
                  :options="deviceOptions"
                  :loading="loadingDevices"
                  placeholder="Search devices"
                  @search="scheduleDeviceSearch"
                  @update:value="onDeviceSelectionChange"
                />
                <n-button block type="primary" :loading="loadingStreams" @click="loadStreams">
                  Load streams
                </n-button>
              </n-space>
            </n-card>

            <n-card embedded size="small" title="Stream">
              <n-space vertical :size="10">
                <n-input v-model:value="streamFilter" placeholder="Filter streams" clearable />
                <n-scrollbar style="max-height: 260px">
                  <button
                    v-for="stream in filteredStreamCatalog"
                    :key="stream.key"
                    class="stream-row"
                    :class="{ active: selectedKeys.includes(stream.key) }"
                    type="button"
                    @click="toggleStream(stream.key)"
                  >
                    <span>
                      <strong>{{ stream.key }}</strong>
                      <small>{{ stream.deviceCount }} device · {{ stream.unit ?? "unitless" }}</small>
                    </span>
                    <n-tag size="small" :type="stream.kind === 'sampled' ? 'info' : 'success'">
                      {{ stream.kind }}
                    </n-tag>
                  </button>
                </n-scrollbar>
              </n-space>
            </n-card>

            <n-card v-if="availableChannels.length > 0" embedded size="small" title="Channels">
              <n-checkbox-group v-model:value="selectedChannels">
                <n-space vertical>
                  <n-checkbox v-for="channel in availableChannels" :key="channel" :value="channel">
                    {{ channel }}
                  </n-checkbox>
                </n-space>
              </n-checkbox-group>
            </n-card>

            <n-card embedded size="small" title="Time and density">
              <n-space vertical :size="12">
                <n-select
                  v-model:value="rangePreset"
                  :options="rangeOptions"
                  @update:value="applyRangePreset"
                />
                <p class="field-hint">Custom dates use your browser's local timezone.</p>
                <n-grid cols="2" :x-gap="8">
                  <n-grid-item>
                    <n-date-picker
                      v-model:value="customFromMs"
                      type="datetime"
                      placeholder="From"
                      clearable
                      style="width: 100%"
                    />
                  </n-grid-item>
                  <n-grid-item>
                    <n-date-picker
                      v-model:value="customToMs"
                      type="datetime"
                      placeholder="To"
                      clearable
                      style="width: 100%"
                    />
                  </n-grid-item>
                </n-grid>
                <n-grid cols="2" :x-gap="8">
                  <n-grid-item>
                    <n-input-number
                      v-model:value="maxPoints"
                      :min="100"
                      :max="maxPointsPerRequest"
                      :step="500"
                      style="width: 100%"
                    />
                  </n-grid-item>
                  <n-grid-item>
                    <n-button block type="primary" secondary :loading="loadingSeries" @click="applyCustomRange">
                      Apply range
                    </n-button>
                  </n-grid-item>
                </n-grid>
                <n-switch v-model:value="autoRefetchOnZoom">
                  <template #checked>Zoom refetch on</template>
                  <template #unchecked>Zoom refetch off</template>
                </n-switch>
                <n-switch v-model:value="enablePrefetch">
                  <template #checked>Adjacent prefetch on</template>
                  <template #unchecked>Adjacent prefetch off</template>
                </n-switch>
              </n-space>
            </n-card>

            <n-card embedded size="small" title="Read model">
              <n-descriptions :column="1" size="small">
                <n-descriptions-item label="Query API">{{ normalizedQueryUrl }}</n-descriptions-item>
                <n-descriptions-item label="Auth">{{ authLabel }}</n-descriptions-item>
                <n-descriptions-item label="Last request">{{ lastRequestLabel }}</n-descriptions-item>
              </n-descriptions>
            </n-card>
          </n-space>
        </n-drawer-content>
      </n-drawer>
    </section>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { LineChart, ScatterChart } from "echarts/charts";
import { CanvasRenderer } from "echarts/renderers";
import { RefreshCw, SlidersHorizontal } from "lucide-vue-next";
import {
  NButton,
  NCard,
  NCheckbox,
  NCheckboxGroup,
  NConfigProvider,
  NDatePicker,
  NDescriptions,
  NDescriptionsItem,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NGrid,
  NGridItem,
  NIcon,
  NInput,
  NInputNumber,
  NScrollbar,
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  createDiscreteApi,
} from "naive-ui";
import type { SelectOption } from "naive-ui";

echarts.use([GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, LineChart, ScatterChart, CanvasRenderer]);

type StreamKind = "scalar" | "sampled";
type ScalarValueType = "double" | "float" | "int" | "bool" | "string";
type TokenProvider = () => string | Promise<string>;

interface StreamInfo {
  key: string;
  kind: StreamKind;
  unit: string | null;
  latest_event_time: string;
  value_type?: ScalarValueType | null;
  channels?: Array<{ key: string; unit?: string | null }>;
  nominal_rate_hz?: number | null;
}

interface SeriesPoint {
  ts: string;
  value?: number;
  text?: string;
  min?: number;
  max?: number;
  avg?: number | null;
}

interface SeriesResponse {
  device_id: string;
  key: string;
  kind: StreamKind;
  value_type?: ScalarValueType | null;
  resolution: string;
  mode?: string;
  points?: SeriesPoint[];
  channels?: Array<{ name: string; unit?: string | null; points: SeriesPoint[] }>;
}

interface StreamCatalogItem {
  key: string;
  kind: StreamKind;
  unit: string | null;
  valueType: ScalarValueType | null;
  deviceCount: number;
  channels: string[];
}

const props = withDefaults(
  defineProps<{
    queryServerUrl: string;
    authToken?: string;
    tokenProvider?: TokenProvider;
    deviceId?: string;
    initialDeviceIds?: string[];
    initialStreamKey?: string;
    initialRangePreset?: string;
    maxPointsPerRequest?: number;
    autoOpenControls?: boolean;
    panelTitle?: string;
  }>(),
  {
    authToken: "",
    tokenProvider: undefined,
    deviceId: "",
    initialDeviceIds: () => [],
    initialStreamKey: "",
    initialRangePreset: "1h",
    maxPointsPerRequest: 10000,
    autoOpenControls: false,
    panelTitle: "AETUS Stream Viewer",
  },
);

const emit = defineEmits<{
  "range-change": [range: { from: string; to: string }];
  "device-change": [devices: string[]];
  "stream-change": [key: string];
  "query-start": [request: { devices: string[]; key: string; from: string; to: string; maxPoints: number }];
  "query-success": [response: { devices: string[]; key: string; pointCount: number }];
  "query-error": [error: Error];
  "auth-expired": [];
  "density-change": [density: { maxPoints: number; reason: string }];
}>();

const { message } = createDiscreteApi(["message"]);
const chartEl = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;
let activeRequest: AbortController | null = null;
let zoomTimer: number | undefined;
let wheelZoomTimer: number | undefined;
let pendingWheelZoomAnchorRatio = 0.5;
let pendingWheelZoomFactor = 1;
let panState:
  | {
      startX: number;
      startFromMs: number;
      startToMs: number;
      latestRange: { from: string; to: string } | null;
      moved: boolean;
    }
  | undefined;
let renderingChart = false;
let suppressDataZoomUntil = 0;

const WHEEL_ZOOM_FACTOR = 1.8;
const WHEEL_ZOOM_DEBOUNCE_MS = 220;
const CHART_GRID_LEFT_PX = 52;
const CHART_GRID_RIGHT_PX = 28;
const DEVICE_SEARCH_DEBOUNCE_MS = 250;
const CHART_COLORS = ["#2563eb", "#06b6d4", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0f766e", "#be123c"];

const normalizedQueryUrl = computed(() => props.queryServerUrl.replace(/\/$/, ""));
const drawerOpen = ref(props.autoOpenControls);
const drawerWidth = computed(() => (window.innerWidth < 720 ? Math.max(window.innerWidth - 24, 320) : 420));
const selectedDeviceIds = ref<string[]>(initialDevices());
const deviceOptions = ref<SelectOption[]>(deviceIdsToOptions(selectedDeviceIds.value));
const streamsByDevice = ref<Record<string, StreamInfo[]>>({});
const selectedKeys = ref<string[]>(props.initialStreamKey ? [props.initialStreamKey] : []);
const streamFilter = ref("");
const seriesResponses = ref<SeriesResponse[]>([]);
const maxPoints = ref(props.maxPointsPerRequest);
const maxPointsPerRequest = computed(() => props.maxPointsPerRequest);
const rangePreset = ref(props.initialRangePreset);
const loadingDevices = ref(false);
const loadingStreams = ref(false);
const loadingSeries = ref(false);
const pendingRangeFetch = ref(false);
const loading = computed(() => loadingStreams.value || loadingSeries.value || pendingRangeFetch.value);
const autoRefetchOnZoom = ref(true);
const enablePrefetch = ref(true);
const selectedChannels = ref<string[]>([]);
const visibleRange = ref(currentRange());
const customFromMs = ref<number | null>(Date.parse(visibleRange.value.from));
const customToMs = ref<number | null>(Date.parse(visibleRange.value.to));
const lastRequestLabel = ref("-");
const authLabel = computed(() => (props.authToken || props.tokenProvider ? "JWT bearer" : "none"));
const panelTitle = computed(() => props.panelTitle);

const rangeOptions = [
  { label: "10 minutes", value: "10m" },
  { label: "1 hour", value: "1h" },
  { label: "6 hours", value: "6h" },
  { label: "1 day", value: "1d" },
  { label: "Custom range", value: "custom" },
];

const streamCatalog = computed<StreamCatalogItem[]>(() => {
  const map = new Map<string, StreamCatalogItem>();
  for (const streams of Object.values(streamsByDevice.value)) {
    for (const stream of streams) {
      const existing = map.get(stream.key);
      const channels = (stream.channels ?? []).map((channel) => channel.key);
      if (existing) {
        existing.deviceCount += 1;
        existing.channels = Array.from(new Set([...existing.channels, ...channels]));
      } else {
        map.set(stream.key, {
          key: stream.key,
          kind: stream.kind,
          unit: stream.unit,
          valueType: stream.value_type ?? null,
          deviceCount: 1,
          channels,
        });
      }
    }
  }
  return Array.from(map.values()).sort((a, b) => a.key.localeCompare(b.key));
});

const filteredStreamCatalog = computed(() => {
  const query = streamFilter.value.trim().toLowerCase();
  if (!query) return streamCatalog.value;
  return streamCatalog.value.filter((stream) => stream.key.toLowerCase().includes(query));
});

const selectedStreamCatalogItems = computed(() => selectedKeys.value.map((key) => streamCatalog.value.find((stream) => stream.key === key)).filter((stream): stream is StreamCatalogItem => Boolean(stream)));
const selectedTitle = computed(() => {
  if (selectedKeys.value.length === 0) return "Stream panel";
  if (selectedKeys.value.length === 1) return selectedKeys.value[0];
  return `${selectedKeys.value.length} streams`;
});
const selectedKind = computed(() => {
  const kinds = new Set(selectedStreamCatalogItems.value.map((stream) => stream.kind));
  if (kinds.size === 1) return selectedStreamCatalogItems.value[0]?.kind ?? null;
  if (kinds.size > 1) return "mixed";
  return seriesResponses.value[0]?.kind ?? null;
});
const availableChannels = computed(() => Array.from(new Set(selectedStreamCatalogItems.value.flatMap((stream) => stream.channels))).sort());
const plottedPointCount = computed(() => seriesResponses.value.reduce((total, response) => total + countSeriesPoints(response), 0));
const seriesResolution = computed(() => {
  const resolutions = Array.from(new Set(seriesResponses.value.map((response) => response.resolution)));
  return resolutions.length ? resolutions.join(" / ") : "-";
});
const visibleRangeLabel = computed(() => `${timeLabel(visibleRange.value.from)} - ${timeLabel(visibleRange.value.to)}`);

onMounted(async () => {
  void searchDevices("");
  await loadStreams();
  window.addEventListener("resize", resizeChart);
  window.addEventListener("aetus-test-zoom", onExternalZoom as EventListener);
  window.addEventListener("aetus-test-datazoom", onExternalDataZoom as EventListener);
  window.addEventListener("aetus-test-wheel-zoomout", onExternalWheelZoomOut as EventListener);
  window.addEventListener("aetus-test-wheel-zoom", onExternalWheelZoom as EventListener);
  window.addEventListener("aetus-test-pan", onExternalPan as EventListener);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", resizeChart);
  window.removeEventListener("aetus-test-zoom", onExternalZoom as EventListener);
  window.removeEventListener("aetus-test-datazoom", onExternalDataZoom as EventListener);
  window.removeEventListener("aetus-test-wheel-zoomout", onExternalWheelZoomOut as EventListener);
  window.removeEventListener("aetus-test-wheel-zoom", onExternalWheelZoom as EventListener);
  window.removeEventListener("aetus-test-pan", onExternalPan as EventListener);
  activeRequest?.abort();
  deviceSearchRequest?.abort();
  if (zoomTimer !== undefined) window.clearTimeout(zoomTimer);
  if (wheelZoomTimer !== undefined) window.clearTimeout(wheelZoomTimer);
  if (deviceSearchTimer !== undefined) window.clearTimeout(deviceSearchTimer);
  chart?.dispose();
  chart = null;
});

watch(
  () => props.authToken,
  () => {
    void refresh();
  },
);

watch(selectedDeviceIds, (devices) => {
  mergeDeviceOptions(devices);
  emit("device-change", [...devices]);
}, { deep: true });

watch(availableChannels, (channels) => {
  selectedChannels.value = [...channels];
});

async function refresh() {
  await loadStreams();
  await loadSeries();
}

async function loadStreams() {
  if (selectedDeviceIds.value.length === 0) return;
  loadingStreams.value = true;
  try {
    const headers = await authHeaders();
    const entries = await Promise.all(
      selectedDeviceIds.value.map(async (deviceId) => {
        const response = await fetch(`${normalizedQueryUrl.value}/v1/query/devices/${encodeURIComponent(deviceId)}/streams`, {
          headers,
        });
        await assertOk(response);
        const body = (await response.json()) as { streams: StreamInfo[] };
        return [deviceId, body.streams] as const;
      }),
    );
    streamsByDevice.value = Object.fromEntries(entries);
    selectedKeys.value = selectedKeys.value.filter((key) => streamCatalog.value.some((stream) => stream.key === key));
    if (selectedKeys.value.length === 0) {
      const firstKey = streamCatalog.value[0]?.key;
      selectedKeys.value = firstKey ? [firstKey] : [];
    }
    await loadSeries();
  } catch (error) {
    handleError(error, "Failed to load streams");
  } finally {
    loadingStreams.value = false;
  }
}

let deviceSearchTimer: number | undefined;
let deviceSearchRequest: AbortController | null = null;

function scheduleDeviceSearch(query: string) {
  if (deviceSearchTimer !== undefined) window.clearTimeout(deviceSearchTimer);
  deviceSearchTimer = window.setTimeout(() => {
    void searchDevices(query);
  }, DEVICE_SEARCH_DEBOUNCE_MS);
}

async function searchDevices(query: string) {
  deviceSearchRequest?.abort();
  deviceSearchRequest = new AbortController();
  loadingDevices.value = true;
  try {
    const headers = await authHeaders();
    const params = new URLSearchParams({ search: query.trim(), limit: "30" });
    const response = await fetch(`${normalizedQueryUrl.value}/v1/query/devices?${params}`, {
      headers,
      signal: deviceSearchRequest.signal,
    });
    await assertOk(response);
    const body = (await response.json()) as { devices: Array<{ device_id: string }> };
    mergeDeviceOptions(body.devices.map((device) => device.device_id));
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return;
    handleError(error, "Failed to search devices");
  } finally {
    loadingDevices.value = false;
  }
}

function mergeDeviceOptions(deviceIds: string[]) {
  const map = new Map<string, SelectOption>();
  for (const option of deviceOptions.value) {
    if (typeof option.value === "string") map.set(option.value, option);
  }
  for (const deviceId of selectedDeviceIds.value) {
    map.set(deviceId, { label: deviceId, value: deviceId });
  }
  for (const deviceId of deviceIds) {
    map.set(deviceId, { label: deviceId, value: deviceId });
  }
  deviceOptions.value = Array.from(map.values()).sort((a, b) => String(a.value).localeCompare(String(b.value)));
}

async function loadSeries(range = visibleRange.value, reason = "manual") {
  if (selectedDeviceIds.value.length === 0 || selectedKeys.value.length === 0) {
    seriesResponses.value = [];
    await nextTick();
    renderChart();
    return;
  }
  activeRequest?.abort();
  activeRequest = new AbortController();
  pendingRangeFetch.value = false;
  loadingSeries.value = true;
  visibleRange.value = range;
  syncCustomRange(range);
  const requestMaxPoints = autoMaxPoints(reason);
  maxPoints.value = requestMaxPoints;
  const requestKeys = [...selectedKeys.value];
  const requestKeyLabel = requestKeys.join(", ");
  emit("density-change", { maxPoints: requestMaxPoints, reason });
  emit("range-change", range);
  emit("query-start", { devices: [...selectedDeviceIds.value], key: requestKeyLabel, ...range, maxPoints: requestMaxPoints });
  try {
    const headers = await authHeaders();
    const params = new URLSearchParams({ from: range.from, to: range.to, max_points: String(requestMaxPoints) });
    const responses = await Promise.all(
      selectedDeviceIds.value.flatMap((deviceId) =>
        requestKeys.map(async (key) => {
          if (!deviceHasStream(deviceId, key)) return null;
          const response = await fetch(
            `${normalizedQueryUrl.value}/v1/query/devices/${encodeURIComponent(deviceId)}/streams/${encodeURIComponent(key)}/series?${params}`,
            { headers, signal: activeRequest?.signal },
          );
          await assertOk(response);
          return (await response.json()) as SeriesResponse;
        }),
      ),
    );
    seriesResponses.value = responses.filter((response): response is SeriesResponse => response !== null);
    lastRequestLabel.value = `${seriesResponses.value.length} response · ${requestKeys.length} stream · ${requestMaxPoints.toLocaleString()} max points`;
    await nextTick();
    renderChart();
    emit("query-success", { devices: [...selectedDeviceIds.value], key: requestKeyLabel, pointCount: plottedPointCount.value });
    if (enablePrefetch.value) void prefetchAdjacent(range, requestMaxPoints);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return;
    handleError(error, "Failed to load series");
  } finally {
    loadingSeries.value = false;
    pendingRangeFetch.value = false;
  }
}

function toggleStream(key: string) {
  selectedKeys.value = selectedKeys.value.includes(key)
    ? selectedKeys.value.filter((selectedKey) => selectedKey !== key)
    : [...selectedKeys.value, key];
  emit("stream-change", selectedKeys.value.join(", "));
  void loadSeries();
}

function onDeviceSelectionChange() {
  void loadStreams();
}

function applyRangePreset() {
  if (rangePreset.value === "custom") {
    applyCustomRange();
    return;
  }
  void loadSeries(currentRange(), "preset");
}

function applyCustomRange() {
  if (customFromMs.value === null || customToMs.value === null) {
    message.error("Select both start and end time");
    return;
  }
  const range = normalizedRange(customFromMs.value, customToMs.value);
  if (!range) {
    message.error("Select a valid time range");
    return;
  }
  rangePreset.value = "custom";
  void loadSeries(range, "custom");
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

function timeLabel(value: string) {
  return formatLocalTimestamp(value, { date: false, milliseconds: false });
}

function autoMaxPoints(reason: string) {
  const width = chartEl.value?.clientWidth ?? 900;
  const pixelBudget = Math.max(1500, Math.floor(width * 12));
  const desired = ["zoom", "zoom-out", "pan"].includes(reason)
    ? Math.max(pixelBudget, maxPoints.value)
    : Math.max(pixelBudget, props.maxPointsPerRequest);
  return Math.min(props.maxPointsPerRequest, desired);
}

async function authHeaders() {
  const token = props.authToken || (props.tokenProvider ? await props.tokenProvider() : "");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function assertOk(response: Response) {
  if (response.status === 401) emit("auth-expired");
  if (!response.ok) throw new Error(`query request failed: ${response.status}`);
}

function handleError(error: unknown, fallback: string) {
  const normalized = error instanceof Error ? error : new Error(fallback);
  emit("query-error", normalized);
  message.error(normalized.message || fallback);
}

function renderChart() {
  if (!chartEl.value) return;
  chart ??= echarts.init(chartEl.value);
  chart.off("datazoom");
  chart.on("datazoom", handleChartZoom);
  chart.getZr().off("mousewheel", handleWheelZoom);
  chart.getZr().on("mousewheel", handleWheelZoom);
  chart.getZr().off("mousedown", handlePanStart);
  chart.getZr().off("mousemove", handlePanMove);
  chart.getZr().off("mouseup", handlePanEnd);
  chart.getZr().off("globalout", handlePanEnd);
  chart.getZr().on("mousedown", handlePanStart);
  chart.getZr().on("mousemove", handlePanMove);
  chart.getZr().on("mouseup", handlePanEnd);
  chart.getZr().on("globalout", handlePanEnd);
  const legendNames = logicalSeriesNames();
  const colorByName = colorMapForNames(legendNames);
  const chartSeries = seriesResponses.value.flatMap((response) => responseToChartSeries(response, colorByName));
  renderingChart = true;
  chart.setOption(
    {
      animation: false,
      color: CHART_COLORS,
      tooltip: { trigger: "axis", formatter: chartTooltipFormatter },
      legend: { top: 10, type: "scroll", data: legendNames.map((name) => legendItemForName(name, colorByName)) },
      grid: { left: 52, right: 28, top: 58, bottom: 58 },
      dataZoom: [
        {
          type: "inside",
          filterMode: "none",
          throttle: 160,
          zoomOnMouseWheel: false,
          moveOnMouseWheel: false,
          moveOnMouseMove: false,
          preventDefaultMouseMove: true,
        },
        {
          type: "slider",
          filterMode: "none",
          height: 24,
          bottom: 16,
          brushSelect: true,
          realtime: true,
          throttle: 160,
        },
      ],
      xAxis: { type: "time", min: visibleRange.value.from, max: visibleRange.value.to },
      yAxis: [
        { type: "value", scale: true },
        { type: "value", min: 0, max: 1, show: false },
      ],
      series: chartSeries,
    },
    true,
  );
  window.setTimeout(() => {
    renderingChart = false;
  }, 0);
}

function chartTooltipFormatter(params: unknown) {
  const items = Array.isArray(params) ? params : [params];
  const visibleItems = filterTooltipItems(items).slice(0, 12);
  if (visibleItems.length === 0) return "";
  const firstValue = (visibleItems[0] as { value?: unknown }).value;
  const timestamp = Array.isArray(firstValue) ? formatTimestampLabel(firstValue[0]) : "";
  const rows = visibleItems.map((item) => {
    const typed = item as { marker?: string; seriesName?: string; value?: unknown };
    const value = typed.value;
    let label = "";
    if (Array.isArray(value) && value.length >= 4) {
      label = `min ${formatNumber(value[2])} / max ${formatNumber(value[3])}`;
    } else if (Array.isArray(value) && typeof value[2] === "string") {
      label = value[2];
    } else if (Array.isArray(value)) {
      label = formatNumber(value[1]);
    } else {
      label = String(value ?? "");
    }
    return `${typed.marker ?? ""}${escapeHtml(typed.seriesName ?? "")}: ${escapeHtml(label)}`;
  });
  return [escapeHtml(timestamp), ...rows].join("<br/>");
}

function filterTooltipItems(items: unknown[]) {
  const hasEnvelopeByName = new Set(
    items
      .filter((item) => {
        const value = (item as { value?: unknown }).value;
        return Array.isArray(value) && value.length >= 4;
      })
      .map((item) => (item as { seriesName?: string }).seriesName)
      .filter((name): name is string => typeof name === "string"),
  );
  return items.filter((item) => {
    const typed = item as { seriesName?: string; value?: unknown };
    if (!typed.seriesName) return false;
    if (!hasEnvelopeByName.has(typed.seriesName)) return true;
    return Array.isArray(typed.value) && typed.value.length >= 4;
  });
}

function stringStateMarkers(points: SeriesPoint[], fallbackText: string) {
  const markers: SeriesPoint[] = [];
  let previousText: string | undefined;
  for (const point of points) {
    const text = point.text ?? fallbackText;
    if (markers.length === 0 || text !== previousText) {
      markers.push({ ...point, text });
    }
    previousText = text;
  }
  return markers;
}

function responseToChartSeries(response: SeriesResponse, colorByName: Map<string, string>) {
  if (response.kind === "scalar") {
    const scalarName = `${response.device_id} / ${response.key}`;
    const scalarColor = colorForName(scalarName, colorByName);
    if (response.value_type === "string") {
      const eventPoints = (response.points ?? []).map((point) => [point.ts, 0.5, point.text ?? ""]);
      return [
        {
          name: scalarName,
          type: "scatter",
          yAxisIndex: 1,
          symbolSize: 8,
          itemStyle: { color: scalarColor },
          data: eventPoints,
          markLine: {
            symbol: "none",
            silent: true,
            label: { formatter: "{b}", rotate: 90, color: "#475569" },
            lineStyle: { type: "dashed", width: 1.5, color: scalarColor },
            data: stringStateMarkers(response.points ?? [], response.key).map((point) => ({
              name: point.text ?? response.key,
              xAxis: point.ts,
            })),
          },
        },
      ];
    }
    return [
      {
        name: scalarName,
        type: "line",
        showSymbol: false,
        sampling: "lttb",
        large: true,
        itemStyle: { color: scalarColor },
        lineStyle: { color: scalarColor },
        data: (response.points ?? []).map((point) => [point.ts, point.value]),
      },
    ];
  }
  return (response.channels ?? [])
    .filter((channel) => selectedChannels.value.length === 0 || selectedChannels.value.includes(channel.name))
    .flatMap((channel) => {
      const name = `${response.device_id} / ${channel.name}`;
      const hasEnvelope = channel.points.some((point) => point.min !== undefined || point.max !== undefined);
      if (!hasEnvelope) {
        const color = colorForName(name, colorByName);
        return [
          {
            name,
            type: "line",
            showSymbol: false,
            sampling: "lttb",
            large: true,
            itemStyle: { color },
            lineStyle: { color },
            data: channel.points.map((point) => [point.ts, point.value]),
          },
        ];
      }
      const color = colorForName(name, colorByName);
      return [
        {
          name,
          type: "line",
          showSymbol: false,
          silent: true,
          sampling: "lttb",
          large: true,
          legendHoverLink: false,
          itemStyle: { color },
          lineStyle: { color, width: 1, opacity: 0.5 },
          emphasis: { disabled: true },
          data: channel.points.map((point) => [point.ts, point.min ?? point.value]),
        },
        {
          name,
          type: "line",
          showSymbol: false,
          sampling: "lttb",
          large: true,
          itemStyle: { color },
          lineStyle: { color, width: 1, opacity: 0.5 },
          emphasis: { focus: "series" },
          data: channel.points.map((point) => [point.ts, point.max ?? point.value, point.min, point.max]),
        },
      ];
    });
}

function logicalSeriesNames() {
  const names: string[] = [];
  const seen = new Set<string>();
  const push = (name: string) => {
    if (seen.has(name)) return;
    seen.add(name);
    names.push(name);
  };
  for (const response of seriesResponses.value) {
    if (response.kind === "scalar") {
      push(`${response.device_id} / ${response.key}`);
      continue;
    }
    for (const channel of response.channels ?? []) {
      if (selectedChannels.value.length > 0 && !selectedChannels.value.includes(channel.name)) continue;
      push(`${response.device_id} / ${channel.name}`);
    }
  }
  return names;
}

function colorMapForNames(names: string[]) {
  return new Map(names.map((name, index) => [name, colorForIndex(index)]));
}

function legendItemForName(name: string, colorByName: Map<string, string>) {
  const color = colorForName(name, colorByName);
  return {
    name,
    icon: "circle",
    itemStyle: { color, borderColor: color },
    lineStyle: { color },
  };
}

function colorForName(name: string, colorByName: Map<string, string>) {
  return colorByName.get(name) ?? colorForIndex(0);
}

function colorForIndex(index: number) {
  if (index < CHART_COLORS.length) return CHART_COLORS[index];
  const hue = (index * 137.508) % 360;
  return `hsl(${hue.toFixed(1)}, 72%, 46%)`;
}

function formatNumber(value: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value ?? "-");
  return Math.abs(value) >= 100 ? value.toFixed(1) : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

function formatTimestampLabel(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `${formatLocalTimestamp(value, { date: true, milliseconds: true })} ${localTimezoneLabel()}`;
  }
  return String(value ?? "");
}

function formatLocalTimestamp(value: string | number | Date, options: { date: boolean; milliseconds: boolean }) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const year = date.getFullYear();
  const month = padDatePart(date.getMonth() + 1);
  const day = padDatePart(date.getDate());
  const hours = padDatePart(date.getHours());
  const minutes = padDatePart(date.getMinutes());
  const seconds = padDatePart(date.getSeconds());
  const millis = String(date.getMilliseconds()).padStart(3, "0");
  const time = `${hours}:${minutes}:${seconds}${options.milliseconds ? `.${millis}` : ""}`;
  return options.date ? `${year}-${month}-${day} ${time}` : time;
}

function padDatePart(value: number) {
  return String(value).padStart(2, "0");
}

function localTimezoneLabel() {
  const offsetMinutes = -new Date().getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const absolute = Math.abs(offsetMinutes);
  const hours = padDatePart(Math.floor(absolute / 60));
  const minutes = padDatePart(absolute % 60);
  return `UTC${sign}${hours}:${minutes}`;
}

function escapeHtml(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function handleChartZoom(event: unknown) {
  if (!autoRefetchOnZoom.value || renderingChart || Date.now() < suppressDataZoomUntil) return;
  const range = zoomRangeFromEvent(event);
  if (!range) return;
  if (zoomTimer !== undefined) window.clearTimeout(zoomTimer);
  zoomTimer = window.setTimeout(() => {
    void loadSeries(range, "zoom");
  }, 450);
}

function handlePanStart(event: unknown) {
  if (!autoRefetchOnZoom.value || renderingChart) return;
  const pointer = event as { offsetX?: number; event?: { preventDefault?: () => void } };
  const startX = pointer.offsetX;
  if (typeof startX !== "number" || !isInsidePlotArea(startX)) return;
  const startFromMs = Date.parse(visibleRange.value.from);
  const startToMs = Date.parse(visibleRange.value.to);
  if (!Number.isFinite(startFromMs) || !Number.isFinite(startToMs) || startToMs <= startFromMs) return;
  pointer.event?.preventDefault?.();
  suppressDataZoomUntil = Date.now() + 700;
  panState = { startX, startFromMs, startToMs, latestRange: null, moved: false };
}

function handlePanMove(event: unknown) {
  if (!panState) return;
  const pointer = event as { offsetX?: number; event?: { preventDefault?: () => void; stopPropagation?: () => void } };
  if (typeof pointer.offsetX !== "number") return;
  const range = pannedRangeFromDelta(pointer.offsetX - panState.startX, panState.startFromMs, panState.startToMs);
  if (!range) return;
  pointer.event?.preventDefault?.();
  pointer.event?.stopPropagation?.();
  suppressDataZoomUntil = Date.now() + 700;
  panState.latestRange = range;
  panState.moved = panState.moved || Math.abs(pointer.offsetX - panState.startX) > 2;
  applyOptimisticRange(range);
}

function handlePanEnd() {
  if (!panState) return;
  const range = panState.moved ? panState.latestRange : null;
  panState = undefined;
  if (!range || !autoRefetchOnZoom.value) return;
  if (zoomTimer !== undefined) window.clearTimeout(zoomTimer);
  zoomTimer = window.setTimeout(() => {
    void loadSeries(range, "pan");
  }, 300);
}

function handleWheelZoom(event: unknown) {
  if (!autoRefetchOnZoom.value || renderingChart) return;
  const wheel = event as {
    offsetX?: number;
    wheelDelta?: number;
    event?: {
      deltaY?: number;
      preventDefault?: () => void;
      stopPropagation?: () => void;
    };
  };
  const factor = wheelZoomFactor(wheel);
  if (factor === null) return;
  wheel.event?.preventDefault?.();
  wheel.event?.stopPropagation?.();
  suppressDataZoomUntil = Date.now() + 700;
  pendingWheelZoomAnchorRatio = wheelAnchorRatio(wheel.offsetX);
  pendingWheelZoomFactor = factor;
  if (wheelZoomTimer !== undefined) window.clearTimeout(wheelZoomTimer);
  wheelZoomTimer = window.setTimeout(flushWheelZoom, WHEEL_ZOOM_DEBOUNCE_MS);
}

function flushWheelZoom() {
  wheelZoomTimer = undefined;
  const range = zoomedRangeFromAnchorRatio(pendingWheelZoomAnchorRatio, pendingWheelZoomFactor);
  if (!range) return;
  applyOptimisticRange(range);
  if (zoomTimer !== undefined) window.clearTimeout(zoomTimer);
  zoomTimer = window.setTimeout(() => {
    void loadSeries(range, pendingWheelZoomFactor > 1 ? "zoom-out" : "zoom");
  }, 350);
}

function applyOptimisticRange(range: { from: string; to: string }) {
  visibleRange.value = range;
  syncCustomRange(range);
  pendingRangeFetch.value = true;
  emit("range-change", range);
  if (!chart) return;
  renderingChart = true;
  chart.setOption(
    {
      xAxis: { min: range.from, max: range.to },
      dataZoom: [
        { start: 0, end: 100 },
        { start: 0, end: 100 },
      ],
    },
    false,
  );
  window.setTimeout(() => {
    renderingChart = false;
  }, 0);
}

function wheelZoomFactor(event: { wheelDelta?: number; event?: { deltaY?: number } }) {
  if (typeof event.event?.deltaY === "number") return event.event.deltaY > 0 ? WHEEL_ZOOM_FACTOR : 1 / WHEEL_ZOOM_FACTOR;
  if (typeof event.wheelDelta === "number") return event.wheelDelta < 0 ? WHEEL_ZOOM_FACTOR : 1 / WHEEL_ZOOM_FACTOR;
  return null;
}

function zoomedRangeFromAnchorRatio(anchorRatio: number, factor: number): { from: string; to: string } | null {
  const fromMs = Date.parse(visibleRange.value.from);
  const toMs = Date.parse(visibleRange.value.to);
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs <= fromMs || factor <= 0) return null;
  const span = toMs - fromMs;
  const nextSpan = span * factor;
  const anchorTime = fromMs + span * anchorRatio;
  const nextFrom = anchorTime - nextSpan * anchorRatio;
  return normalizedRange(nextFrom, nextFrom + nextSpan);
}

function pannedRangeFromDelta(deltaX: number, fromMs: number, toMs: number): { from: string; to: string } | null {
  const width = chartEl.value?.clientWidth ?? 900;
  if (width <= 0) return null;
  const span = toMs - fromMs;
  if (!Number.isFinite(span) || span <= 0) return null;
  const deltaMs = (-deltaX / width) * span;
  return normalizedRange(fromMs + deltaMs, toMs + deltaMs);
}

function isInsidePlotArea(offsetX: number) {
  if (!chartEl.value) return false;
  const width = chartEl.value.clientWidth;
  return offsetX >= CHART_GRID_LEFT_PX && offsetX <= Math.max(CHART_GRID_LEFT_PX, width - CHART_GRID_RIGHT_PX);
}

function wheelAnchorRatio(offsetX: number | undefined) {
  if (!chartEl.value || typeof offsetX !== "number") return 0.5;
  const plotLeft = CHART_GRID_LEFT_PX;
  const plotWidth = Math.max(1, chartEl.value.clientWidth - CHART_GRID_LEFT_PX - CHART_GRID_RIGHT_PX);
  const ratio = (offsetX - plotLeft) / plotWidth;
  return Math.min(0.95, Math.max(0.05, ratio));
}

function zoomRangeFromEvent(event: unknown): { from: string; to: string } | null {
  const payload = event as {
    batch?: Array<{
      start?: number;
      end?: number;
      startValue?: string | number;
      endValue?: string | number;
    }>;
    start?: number;
    end?: number;
    startValue?: string | number;
    endValue?: string | number;
  };
  const item = payload.batch?.[0] ?? payload;
  if (item.startValue !== undefined && item.endValue !== undefined) {
    return normalizedRange(timeValueToMs(item.startValue), timeValueToMs(item.endValue));
  }
  if (typeof item.start === "number" && typeof item.end === "number") {
    return rangeFromPercent(item.start, item.end);
  }
  return null;
}

function rangeFromPercent(startPercent: number, endPercent: number): { from: string; to: string } | null {
  const baseStart = Date.parse(visibleRange.value.from);
  const baseEnd = Date.parse(visibleRange.value.to);
  if (!Number.isFinite(baseStart) || !Number.isFinite(baseEnd) || baseEnd <= baseStart) return null;
  const start = clampPercent(startPercent);
  const end = clampPercent(endPercent);
  const lower = Math.min(start, end);
  const upper = Math.max(start, end);
  if (upper - lower < 0.001) return null;
  const span = baseEnd - baseStart;
  return normalizedRange(baseStart + (span * lower) / 100, baseStart + (span * upper) / 100);
}

function normalizedRange(fromMs: number, toMs: number): { from: string; to: string } | null {
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs <= fromMs) return null;
  return { from: new Date(fromMs).toISOString(), to: new Date(toMs).toISOString() };
}

function timeValueToMs(value: string | number) {
  return typeof value === "number" ? value : Date.parse(value);
}

function clampPercent(value: number) {
  return Math.min(100, Math.max(0, value));
}

function onExternalZoom(event: CustomEvent<{ from: string; to: string }>) {
  if (!event.detail?.from || !event.detail?.to) return;
  void loadSeries(event.detail, "zoom");
}

function onExternalDataZoom(event: CustomEvent<unknown>) {
  handleChartZoom(event.detail);
}

function onExternalWheelZoomOut(event: CustomEvent<{ anchorRatio?: number }>) {
  scheduleExternalWheelZoom(event.detail?.anchorRatio ?? 0.5, WHEEL_ZOOM_FACTOR);
}

function onExternalWheelZoom(event: CustomEvent<{ anchorRatio?: number; direction?: "in" | "out" }>) {
  const factor = event.detail?.direction === "in" ? 1 / WHEEL_ZOOM_FACTOR : WHEEL_ZOOM_FACTOR;
  scheduleExternalWheelZoom(event.detail?.anchorRatio ?? 0.5, factor);
}

function scheduleExternalWheelZoom(anchorRatio: number, factor: number) {
  if (!autoRefetchOnZoom.value) return;
  pendingWheelZoomAnchorRatio = Math.min(0.95, Math.max(0.05, anchorRatio));
  pendingWheelZoomFactor = factor;
  if (wheelZoomTimer !== undefined) window.clearTimeout(wheelZoomTimer);
  wheelZoomTimer = window.setTimeout(flushWheelZoom, WHEEL_ZOOM_DEBOUNCE_MS);
}

function onExternalPan(event: CustomEvent<{ deltaX?: number }>) {
  if (!autoRefetchOnZoom.value) return;
  const fromMs = Date.parse(visibleRange.value.from);
  const toMs = Date.parse(visibleRange.value.to);
  const range = pannedRangeFromDelta(event.detail?.deltaX ?? 0, fromMs, toMs);
  if (!range) return;
  applyOptimisticRange(range);
  if (zoomTimer !== undefined) window.clearTimeout(zoomTimer);
  zoomTimer = window.setTimeout(() => {
    void loadSeries(range, "pan");
  }, 300);
}

function syncCustomRange(range: { from: string; to: string }) {
  const fromMs = Date.parse(range.from);
  const toMs = Date.parse(range.to);
  if (Number.isFinite(fromMs)) customFromMs.value = fromMs;
  if (Number.isFinite(toMs)) customToMs.value = toMs;
}

async function prefetchAdjacent(range: { from: string; to: string }, requestMaxPoints: number) {
  if (selectedKeys.value.length === 0 || selectedDeviceIds.value.length === 0) return;
  const fromMs = Date.parse(range.from);
  const toMs = Date.parse(range.to);
  const span = toMs - fromMs;
  if (!Number.isFinite(span) || span <= 0) return;
  const nextRange = { from: new Date(toMs).toISOString(), to: new Date(toMs + span).toISOString() };
  try {
    const headers = await authHeaders();
    const params = new URLSearchParams({ from: nextRange.from, to: nextRange.to, max_points: String(requestMaxPoints) });
    await Promise.all(
      selectedDeviceIds.value.flatMap((deviceId) =>
        selectedKeys.value.map((key) => {
          if (!deviceHasStream(deviceId, key)) return Promise.resolve();
          return fetch(
            `${normalizedQueryUrl.value}/v1/query/devices/${encodeURIComponent(deviceId)}/streams/${encodeURIComponent(key)}/series?${params}`,
            { headers },
          ).then(() => undefined);
        }),
      ),
    );
  } catch {
    // Prefetch is opportunistic and should never disturb the visible chart.
  }
}

function deviceHasStream(deviceId: string, key: string) {
  return (streamsByDevice.value[deviceId] ?? []).some((stream) => stream.key === key);
}

function countSeriesPoints(response: SeriesResponse) {
  if (response.kind === "scalar") return response.points?.length ?? 0;
  const channels = selectedChannels.value.length > 0
    ? response.channels?.filter((channel) => selectedChannels.value.includes(channel.name))
    : response.channels;
  return channels?.reduce((total, channel) => total + channel.points.length, 0) ?? 0;
}

function initialDevices() {
  const devices = props.initialDeviceIds.length > 0 ? props.initialDeviceIds : props.deviceId ? [props.deviceId] : [];
  return Array.from(new Set(devices));
}

function deviceIdsToOptions(deviceIds: string[]): SelectOption[] {
  return deviceIds.map((deviceId) => ({ label: deviceId, value: deviceId }));
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
    radial-gradient(circle at top left, rgba(83, 161, 255, 0.22), transparent 34rem),
    linear-gradient(135deg, #f4f9ff 0%, #f6f2ff 48%, #fbfdff 100%);
}

.viewer-shell {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 4px 16px;
}

.title-block h1 {
  margin: 2px 0 0;
  font-size: clamp(24px, 4vw, 38px);
  line-height: 1.04;
  letter-spacing: -0.04em;
}

.eyebrow,
.subline {
  margin: 0;
  color: #647084;
}

.eyebrow {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.subline {
  margin-top: 8px;
  font-size: 13px;
}

.chart-panel {
  position: relative;
  overflow: hidden;
  min-height: calc(100vh - 128px);
  border: 1px solid rgba(110, 129, 160, 0.28);
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.78);
  box-shadow: 0 24px 80px rgba(20, 35, 70, 0.12);
  backdrop-filter: blur(16px);
}

.chart-surface {
  width: 100%;
  height: calc(100vh - 150px);
  min-height: 520px;
  cursor: grab;
  touch-action: none;
}

.chart-surface:active {
  cursor: grabbing;
}

.empty-state {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: grid;
  place-content: center;
  gap: 16px;
  background: rgba(255, 255, 255, 0.82);
}

.floating-status {
  position: absolute;
  left: 18px;
  bottom: 18px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.floating-status span {
  border: 1px solid rgba(100, 116, 139, 0.22);
  border-radius: 999px;
  padding: 6px 10px;
  color: #475569;
  background: rgba(255, 255, 255, 0.72);
  font-size: 12px;
  font-weight: 700;
}

.fetch-status {
  min-width: 72px;
}

.fetch-status i {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 6px;
  border-radius: 999px;
  background: #22c55e;
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.14);
  vertical-align: 1px;
}

.fetch-status.active i {
  background: #38bdf8;
  box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.16);
  animation: aetus-fetch-pulse 1.4s ease-in-out infinite;
}

.device-select :deep(.n-base-selection-input-tag) {
  display: inline-block;
  flex: 1 1 140px;
  min-width: 140px;
}

.device-select :deep(.n-base-selection-input-tag__input) {
  min-width: 120px;
}

@keyframes aetus-fetch-pulse {
  0%,
  100% {
    opacity: 0.45;
    transform: scale(0.9);
  }

  50% {
    opacity: 1;
    transform: scale(1.15);
  }
}

.stream-row {
  width: 100%;
  border: 1px solid #dde5ef;
  border-radius: 12px;
  padding: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  background: #fff;
  cursor: pointer;
  text-align: left;
}

.stream-row + .stream-row {
  margin-top: 8px;
}

.stream-row.active {
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.14);
}

.stream-row strong,
.stream-row small {
  display: block;
}

.stream-row small {
  margin-top: 2px;
  color: #667085;
}

@media (max-width: 720px) {
  .aetus-stream-viewer {
    padding: 10px;
  }

  .viewer-shell {
    align-items: flex-start;
  }

  .chart-panel {
    min-height: calc(100vh - 120px);
    border-radius: 20px;
  }

  .chart-surface {
    height: calc(100vh - 130px);
    min-height: 420px;
  }
}
</style>
