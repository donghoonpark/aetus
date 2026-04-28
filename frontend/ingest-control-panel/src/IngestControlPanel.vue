<template>
  <n-space vertical :size="20">
    <n-card embedded class="hero-card">
      <n-flex justify="space-between" align="center" wrap>
        <n-space align="center" :size="16">
          <div class="brand-mark">
            <span class="brand-icon">🦅</span>
          </div>
          <div>
            <n-tag round type="warning" size="small">AETUS Flight Control</n-tag>
            <h1 class="hero-title">{{ title }}</h1>
            <p class="hero-subtitle">Portable Vue + Naive UI control panel for ingest operations.</p>
          </div>
        </n-space>
        <n-space vertical :size="4" align="end">
          <n-text depth="3">Server: {{ normalizedServerUrl }}</n-text>
          <n-button tertiary size="small" @click="refreshAll" :loading="loading.status || loading.devices">
            Refresh
          </n-button>
        </n-space>
      </n-flex>
    </n-card>

    <n-grid cols="1 s:2 l:5" responsive="screen" :x-gap="16" :y-gap="16">
      <n-grid-item v-for="status in statuses" :key="status.name">
        <n-card size="small" embedded>
          <n-space vertical :size="8">
            <n-space justify="space-between" align="center">
              <n-text strong>{{ labelFor(status.name) }}</n-text>
              <span class="signal-dot" :class="`signal-${signalClass(status.state)}`"></span>
            </n-space>
            <n-tag size="small" :type="tagType(status.state)">
              {{ status.state }}
            </n-tag>
            <n-ellipsis :line-clamp="2">
              <n-text depth="3">{{ status.detail }}</n-text>
            </n-ellipsis>
          </n-space>
        </n-card>
      </n-grid-item>
    </n-grid>

    <n-grid cols="1 l:12" responsive="screen" :x-gap="16" :y-gap="16">
      <n-grid-item :span="5">
        <n-card title="Issue Device Token" embedded>
          <n-form :model="issueForm" label-placement="top" @submit.prevent="issueDevice">
            <n-form-item label="Hardware ID">
              <n-input v-model:value="issueForm.hardware_id" placeholder="esp32c5-a1b2c3d4e5f6" />
            </n-form-item>
            <n-form-item label="Model">
              <n-input v-model:value="issueForm.model" />
            </n-form-item>
            <n-form-item label="Firmware Version">
              <n-input-number v-model:value="issueForm.firmware_version" :show-button="false" style="width: 100%" />
            </n-form-item>
            <n-form-item label="Site Code">
              <n-input v-model:value="issueForm.site_code" placeholder="factory-a" />
            </n-form-item>
            <n-space vertical :size="12">
              <n-button type="primary" block :loading="loading.issue" @click="issueDevice">
                Issue Token
              </n-button>
              <n-alert v-if="issuedDevice" type="success" title="Issued successfully">
                <n-space vertical :size="8">
                  <n-text><strong>{{ issuedDevice.device_id }}</strong> for {{ issuedDevice.hardware_id }}</n-text>
                  <n-flex justify="space-between" align="center">
                    <code class="token-code">{{ issuedDevice.token }}</code>
                    <n-button size="small" secondary @click="copyToken(issuedDevice.token)">Copy</n-button>
                  </n-flex>
                </n-space>
              </n-alert>
            </n-space>
          </n-form>
        </n-card>
      </n-grid-item>

      <n-grid-item :span="7">
        <n-card embedded>
          <template #header>
            <n-flex justify="space-between" align="center" wrap>
              <n-space align="center">
                <span>Issued Devices</span>
                <n-tag size="small" type="info">{{ totalItems }} devices</n-tag>
              </n-space>
              <n-space>
                <n-input
                  v-model:value="query"
                  clearable
                  placeholder="Search device, hardware, model, site"
                  style="width: 280px"
                  @keyup.enter="applyQuery"
                  @clear="applyQuery"
                />
                <n-button secondary @click="applyQuery">Search</n-button>
              </n-space>
            </n-flex>
          </template>

          <n-data-table
            :columns="columns"
            :data="devices"
            :loading="loading.devices"
            :pagination="false"
            :bordered="false"
            size="small"
          />

          <n-flex justify="space-between" align="center" style="margin-top: 16px">
            <n-text depth="3">Page {{ page }} / {{ totalPages }}</n-text>
            <n-pagination
              v-model:page="page"
              :page-count="totalPages"
              :page-size="pageSize"
              @update:page="fetchDevices"
            />
          </n-flex>
        </n-card>
      </n-grid-item>
    </n-grid>
  </n-space>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from "vue";
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NEllipsis,
  NFlex,
  NForm,
  NFormItem,
  NGrid,
  NGridItem,
  NInput,
  NInputNumber,
  NPagination,
  NSpace,
  NTag,
  NText,
  createDiscreteApi,
  type DataTableColumns,
} from "naive-ui";

type StatusState = "healthy" | "degraded" | "down" | "unknown";

interface ComponentStatus {
  name: string;
  state: StatusState;
  detail: string;
}

interface DeviceSummary {
  device_id: string;
  hardware_id: string;
  token: string;
  model: string | null;
  firmware_version: number | null;
  site_code: string | null;
  created_at: string;
  updated_at: string;
}

interface DeviceListResponse {
  items: DeviceSummary[];
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
  query: string;
}

const props = withDefaults(
  defineProps<{
    serverUrl: string;
    title?: string;
    pageSize?: number;
  }>(),
  {
    title: "Ingest Control Panel",
    pageSize: 10,
  },
);

const { message } = createDiscreteApi(["message"]);
const normalizedServerUrl = computed(() => props.serverUrl.replace(/\/$/, ""));
const statuses = ref<ComponentStatus[]>([]);
const devices = ref<DeviceSummary[]>([]);
const issuedDevice = ref<DeviceSummary | null>(null);
const query = ref("");
const page = ref(1);
const pageSize = ref(props.pageSize);
const totalItems = ref(0);
const totalPages = ref(1);
const loading = reactive({
  status: false,
  devices: false,
  issue: false,
});

const issueForm = reactive({
  hardware_id: "",
  model: "esp32-c5",
  firmware_version: 1002003 as number | null,
  site_code: "",
});

const columns = computed<DataTableColumns<DeviceSummary>>(() => [
  { title: "Device", key: "device_id" },
  { title: "Hardware ID", key: "hardware_id", ellipsis: true },
  { title: "Site", key: "site_code", render: (row) => row.site_code ?? "-" },
  {
    title: "Token",
    key: "token",
    render: (row) =>
      h(
        NFlex,
        { justify: "space-between", align: "center", size: 8, wrap: false },
        {
          default: () => [
            h("code", { class: "token-code token-inline" }, row.token),
            h(
              NButton,
              { size: "tiny", secondary: true, onClick: () => copyToken(row.token) },
              { default: () => "Copy" },
            ),
          ],
        },
      ),
  },
  { title: "Updated", key: "updated_at", ellipsis: true },
]);

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${normalizedServerUrl.value}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

async function fetchStatus() {
  loading.status = true;
  try {
    const response = await fetchJson<{ components: ComponentStatus[] }>("/v1/control/status");
    statuses.value = response.components;
  } catch (error) {
    message.error(`Failed to load status: ${(error as Error).message}`);
  } finally {
    loading.status = false;
  }
}

async function fetchDevices() {
  loading.devices = true;
  try {
    const params = new URLSearchParams({
      page: String(page.value),
      page_size: String(pageSize.value),
      q: query.value,
    });
    const response = await fetchJson<DeviceListResponse>(`/v1/control/devices?${params.toString()}`);
    devices.value = response.items;
    totalItems.value = response.total_items;
    totalPages.value = response.total_pages;
    page.value = response.page;
  } catch (error) {
    message.error(`Failed to load devices: ${(error as Error).message}`);
  } finally {
    loading.devices = false;
  }
}

async function issueDevice() {
  if (!issueForm.hardware_id.trim()) {
    message.warning("Hardware ID is required");
    return;
  }
  loading.issue = true;
  try {
    issuedDevice.value = await fetchJson<DeviceSummary>("/v1/control/devices/issue", {
      method: "POST",
      body: JSON.stringify({
        hardware_id: issueForm.hardware_id.trim(),
        model: issueForm.model.trim() || "esp32-c5",
        firmware_version: issueForm.firmware_version,
        site_code: issueForm.site_code.trim() || null,
      }),
    });
    message.success("Device token issued");
    await fetchDevices();
    await fetchStatus();
  } catch (error) {
    message.error(`Failed to issue token: ${(error as Error).message}`);
  } finally {
    loading.issue = false;
  }
}

async function copyToken(token: string) {
  await navigator.clipboard.writeText(token);
  message.success("Token copied");
}

function applyQuery() {
  page.value = 1;
  void fetchDevices();
}

function refreshAll() {
  void Promise.all([fetchStatus(), fetchDevices()]);
}

function labelFor(name: string): string {
  return {
    api: "API",
    control_db: "Control DB",
    kafka: "Kafka",
    kafka_connect: "Kafka Connect",
    postgres: "PostgreSQL",
  }[name] ?? name;
}

function tagType(state: StatusState) {
  if (state === "healthy") return "success";
  if (state === "degraded") return "warning";
  if (state === "down") return "error";
  return "default";
}

function signalClass(state: StatusState) {
  if (state === "healthy") return "green";
  if (state === "degraded") return "yellow";
  if (state === "down") return "red";
  return "gray";
}

onMounted(() => {
  refreshAll();
});
</script>

<style scoped>
.hero-card {
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.84);
  backdrop-filter: blur(10px);
  box-shadow: 0 24px 48px rgba(15, 23, 42, 0.1);
}

.brand-mark {
  width: 64px;
  height: 64px;
  border-radius: 18px;
  display: grid;
  place-items: center;
  background: linear-gradient(145deg, #12324a, #204e72);
  box-shadow: 0 18px 28px rgba(18, 50, 74, 0.25);
}

.brand-icon {
  font-size: 28px;
}

.hero-title {
  margin: 10px 0 6px;
  font-size: 28px;
  font-weight: 700;
  color: #17324a;
}

.hero-subtitle {
  margin: 0;
  color: #5f7287;
}

.signal-dot {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  display: inline-block;
  box-shadow: 0 0 0 5px rgba(0, 0, 0, 0.04);
}

.signal-green {
  background: #18a058;
}

.signal-yellow {
  background: #f0a020;
}

.signal-red {
  background: #d03050;
}

.signal-gray {
  background: #9ca3af;
}

.token-code {
  display: inline-block;
  padding: 6px 10px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid rgba(15, 23, 42, 0.08);
  font-size: 12px;
}

.token-inline {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
