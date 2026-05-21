<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import type { Device } from "../types";

const router = useRouter();
const devices = ref<Device[]>([]);
const loading = ref(false);
import { API_BASE, WS_URL } from "../config";
const latestData = ref<Record<string, Record<string, number>>>({});

let ws: WebSocket | null = null;
let refreshTimer: number | null = null;

function connectWs() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  ws = new WebSocket(WS_URL);
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "realtime") latestData.value = msg.data;
  };
  ws.onclose = () => setTimeout(connectWs, 3000);
}

async function fetchDevices() {
  loading.value = true;
  try {
    const res = await fetch(`${API_BASE}/api/devices`);
    devices.value = await res.json();
    for (const d of devices.value) {
      try {
        const r = await fetch(`${API_BASE}/api/devices/${d.device_id}/realtime`);
        const j = await r.json();
        if (j.data) latestData.value[d.device_id] = j.data;
      } catch {}
    }
  } catch {} finally { loading.value = false; }
}

onMounted(() => { fetchDevices(); connectWs(); refreshTimer = window.setInterval(fetchDevices, 10000); });
onUnmounted(() => { ws?.close(); if (refreshTimer) clearInterval(refreshTimer); });

function getDeviceData(id: string) { return latestData.value[id] || {}; }

function fmtPower(w: number) { return Math.abs(w) >= 1000 ? (w / 1000).toFixed(1) + " kW" : w.toFixed(0) + " W"; }

function statusText(d: Device, data: Record<string, number>): string {
  const st = data.status ?? 0;
  switch (d.dev_type) {
    case "pv_inverter": return st === 1 ? "运行中" : st === 2 ? "故障" : "待机";
    case "battery_pcs": return st === 1 ? "充电中" : st === 2 ? "放电中" : st === 3 ? "故障" : "待机";
    case "chp": return st === 1 ? "运行中" : "停机";
    case "heatpump": return data.mode === 1 ? "制热中" : data.mode === 2 ? "制冷中" : "待机";
    case "thermal_storage": return ["待机","蓄热中","放热中","蓄冷中","放冷中"][data.mode ?? 0] || "待机";
    case "smart_meter": case "env_sensor": case "pipe_sensor": return "在线监测";
    default: return "在线";
  }
}

function statusColor(d: Device, data: Record<string, number>): string {
  const st = data.status ?? 0;
  const mode = data.mode ?? 0;
  if (d.dev_type === "pv_inverter") return st === 1 ? "#34c759" : st === 2 ? "#ff3b30" : "#8e8e93";
  if (d.dev_type === "battery_pcs") return (st === 1 || st === 2) ? "#34c759" : st === 3 ? "#ff3b30" : "#8e8e93";
  if (d.dev_type === "chp") return st === 1 ? "#34c759" : "#8e8e93";
  if (d.dev_type === "heatpump") return mode === 3 ? "#ff3b30" : mode === 0 ? "#8e8e93" : "#34c759";
  if (d.dev_type === "thermal_storage") return mode === 0 ? "#8e8e93" : "#34c759";
  if (d.dev_type === "smart_meter" || d.dev_type === "env_sensor" || d.dev_type === "pipe_sensor") return "#34c759";
  return "#8e8e93";
}

function primaryMetric(d: Device, data: Record<string, number>): { val: string; label: string } {
  switch (d.dev_type) {
    case "pv_inverter": return { val: fmtPower(data.active_power ?? 0), label: "发电功率" };
    case "battery_pcs": return { val: fmtPower(data.active_power ?? 0), label: "充放功率" };
    case "chp": return { val: fmtPower(data.active_power ?? 0), label: "发电功率" };
    case "heatpump": return { val: fmtPower(data.thermal_power ?? 0), label: data.mode === 2 ? "制冷量" : "制热量" };
    case "thermal_storage": return { val: fmtPower(data.power ?? 0), label: "蓄放功率" };
    case "smart_meter": return { val: fmtPower(data.active_power ?? 0), label: "实时功率" };
    case "env_sensor": return { val: (data.temperature ?? 0).toFixed(1) + "°C", label: "温度" };
    case "pipe_sensor": return { val: (data.temp_supply ?? 0).toFixed(1) + "°C", label: "供水温度" };
    default: return { val: "-", label: "" };
  }
}

function secondaryMetric(d: Device, data: Record<string, number>): string {
  switch (d.dev_type) {
    case "pv_inverter": return (data.daily_energy ?? 0).toFixed(1) + " kWh 今日";
    case "battery_pcs": return "SOC " + (data.soc ?? 0).toFixed(0) + "%";
    case "chp": return (data.heat_power ?? 0) >= 1000 ? (data.heat_power / 1000).toFixed(1) + " kW 余热" : "";
    case "heatpump": return "COP " + (data.cop ?? 0).toFixed(1);
    case "thermal_storage": return "储热 " + (data.heat_soc ?? 0).toFixed(0) + "% · 储冷 " + (data.cool_soc ?? 0).toFixed(0) + "%";
    case "smart_meter": return (data.total_energy ?? 0).toFixed(1) + " kWh 累计";
    case "env_sensor": return "湿度 " + (data.humidity ?? 0).toFixed(0) + "% · CO₂ " + (data.co2 ?? 0).toFixed(0) + "ppm";
    case "pipe_sensor": return "回水 " + (data.temp_return ?? 0).toFixed(1) + "°C · 流量 " + (data.flow_rate ?? 0).toFixed(1) + "m³/h";
    default: return "";
  }
}
</script>

<template>
  <div>
    <!-- Hero -->
    <section class="hero-strip" style="padding: 64px 24px 56px">
      <div class="hero-content">
        <h1 style="font-family:var(--font-display);font-size:40px;font-weight:600;line-height:1.1;color:#fff;margin:0">设备监控</h1>
        <p style="font-family:var(--font-display);font-size:21px;font-weight:400;line-height:1.19;letter-spacing:0.231px;color:rgba(255,255,255,0.64);margin-top:8px">{{ devices.length }} 台设备在线</p>
      </div>
    </section>

    <!-- Product Grid -->
    <section class="page" v-loading="loading">
      <div class="device-grid" v-if="devices.length">
        <div v-for="device in devices" :key="device.device_id"
             class="device-tile" @click="router.push(`/devices/${device.device_id}`)">
          <div class="tile-header">
            <span class="tile-name">{{ device.name }}</span>
            <span class="tile-status" :style="{color: statusColor(device, getDeviceData(device.device_id))}">
              ● {{ statusText(device, getDeviceData(device.device_id)) }}
            </span>
          </div>
          <div class="tile-body">
            <div class="tile-primary">{{ primaryMetric(device, getDeviceData(device.device_id)).val }}</div>
            <div class="tile-label">{{ primaryMetric(device, getDeviceData(device.device_id)).label }}</div>
            <div class="tile-secondary" v-if="secondaryMetric(device, getDeviceData(device.device_id))">
              {{ secondaryMetric(device, getDeviceData(device.device_id)) }}
            </div>
          </div>
          <div class="tile-footer">
            <span class="tile-link">查看详情 →</span>
          </div>
        </div>
      </div>
      <el-empty v-else description="暂无设备" />
    </section>
  </div>
</template>

<style scoped>
.hero-strip { background: var(--apple-black); }
.hero-content { max-width: var(--content-max); margin: 0 auto; }

.device-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 20px;
}
.device-tile {
  background: var(--surface-card);
  border-radius: 12px;
  padding: 24px;
  box-shadow: var(--shadow-subtle);
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.3s;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.device-tile:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-card);
}
.tile-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.tile-name {
  font-family: var(--font-display);
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.374px;
  color: var(--apple-text);
}
.tile-status {
  font-size: 12px;
  font-weight: 500;
  letter-spacing: -0.12px;
}
.tile-body { flex: 1; }
.tile-primary {
  font-family: var(--font-display);
  font-size: 34px;
  font-weight: 600;
  line-height: 1.1;
  color: var(--apple-text);
  margin-bottom: 4px;
}
.tile-label {
  font-size: 13px;
  color: var(--apple-text-secondary);
  letter-spacing: -0.224px;
}
.tile-secondary {
  font-size: 13px;
  color: var(--apple-text-tertiary);
  margin-top: 6px;
}
.tile-footer {
  padding-top: 8px;
  border-top: 1px solid rgba(0,0,0,0.04);
}
.tile-link {
  font-size: 14px;
  color: var(--apple-link);
  letter-spacing: -0.224px;
}
</style>
