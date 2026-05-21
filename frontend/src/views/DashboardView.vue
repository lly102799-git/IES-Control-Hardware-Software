<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { SankeyChart, BarChart } from "echarts/charts";
import { TitleComponent, TooltipComponent, GridComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

use([SankeyChart, BarChart, TitleComponent, TooltipComponent, GridComponent, CanvasRenderer]);

import { API_BASE, WS_URL } from "../config";

interface Kpi { pv_power_kw: number; bat_power_kw: number; soc: number; self_use_rate: number; chp_power_kw?: number; chp_heat_kw?: number; hp_power_kw?: number; hp_thermal_kw?: number; ts_power_kw?: number; ts_heat_soc?: number; ts_cool_soc?: number }
interface FlowLink { source: string; target: string; value: number }

const kpi = ref<Kpi>({ pv_power_kw: 0, bat_power_kw: 0, soc: 50, self_use_rate: 0 });
const energyFlow = ref<FlowLink[]>([]);
const devices = ref<any[]>([]);
let ws: WebSocket | null = null;
let refreshTimer: number | null = null;

async function fetchOverview() {
  try {
    const r = await fetch(`${API_BASE}/api/dashboard/overview`);
    const d = await r.json();
    kpi.value = d.kpi || kpi.value;
    energyFlow.value = d.energy_flow || [];
    devices.value = d.devices || [];
  } catch {}
}

function connectWs() {
  ws = new WebSocket(WS_URL);
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type !== "realtime") return;
    const d = msg.data;
    if (d.pv_inverter_01) kpi.value.pv_power_kw = Math.round((d.pv_inverter_01.active_power || 0) / 100) / 10;
    if (d.battery_pcs_01) { kpi.value.bat_power_kw = Math.round((d.battery_pcs_01.active_power || 0) / 100) / 10; kpi.value.soc = Math.round((d.battery_pcs_01.soc || 50) * 10) / 10; }
    if (d.chp_01) { kpi.value.chp_power_kw = (d.chp_01.active_power || 0) / 1000; kpi.value.chp_heat_kw = (d.chp_01.heat_power || 0) / 1000; }
    if (d.heatpump_01) { kpi.value.hp_power_kw = (d.heatpump_01.elec_power || 0) / 1000; kpi.value.hp_thermal_kw = (d.heatpump_01.thermal_power || 0) / 1000; }
    if (d.thermal_storage_01) { kpi.value.ts_power_kw = (d.thermal_storage_01.power || 0) / 1000; kpi.value.ts_heat_soc = (d.thermal_storage_01.heat_soc || 0); kpi.value.ts_cool_soc = (d.thermal_storage_01.cool_soc || 0); }
  };
  ws.onclose = () => setTimeout(connectWs, 3000);
}

onMounted(() => { fetchOverview(); connectWs(); refreshTimer = window.setInterval(fetchOverview, 30000); });
onUnmounted(() => { ws?.close(); if (refreshTimer) clearInterval(refreshTimer); });

const sankeyOption = computed(() => ({
  tooltip: { trigger: "item", formatter: (p: any) => `${p.name}: ${p.value?.toFixed(1)} kW` },
  series: [{
    type: "sankey", layout: "none", emphasis: { focus: "adjacency" }, nodeAlign: "left",
    data: [
      { name: "光伏", itemStyle: { color: "#f5a623" } },
      { name: "CHP电", itemStyle: { color: "#e8633a" } },
      { name: "CHP热", itemStyle: { color: "#e8633a" } },
      { name: "电网", itemStyle: { color: "#8e8e93" } },
      { name: "储能", itemStyle: { color: "#0071e3" } },
      { name: "电负荷" },
      { name: "热负荷" },
      { name: "冷负荷" },
      { name: "热泵", itemStyle: { color: "#34c759" } },
      { name: "蓄能", itemStyle: { color: "#ff9f0a" } },
    ],
    links: energyFlow.value.filter((l: any) => l.value > 0.05).map((l: any) => ({...l})),
    label: { fontSize: 12, fontFamily: "SF Pro Text, -apple-system, sans-serif" },
    lineStyle: { color: "gradient", curveness: 0.5 },
  }],
}));

const deviceBarOption = computed(() => ({
  tooltip: { trigger: "axis" },
  xAxis: { type: "category", data: devices.value.map((d: any) => d.name.length > 5 ? d.name.slice(0,5)+"…" : d.name), axisLabel: { fontSize: 11 } },
  yAxis: { type: "value", name: "kW", splitLine: { lineStyle: { color: "rgba(0,0,0,0.06)" } } },
  series: [{ type: "bar", data: devices.value.map((d: any) => ({ value: Math.abs(d.power_kw), itemStyle: { color: d.power_kw >= 0 ? "#34c759" : "#0071e3", borderRadius: [4,4,0,0] } })), barWidth: "50%" }],
}));

const kpiCards = computed(() => [
  { label: "光伏发电", value: kpi.value.pv_power_kw.toFixed(1), unit: "kW", icon: "☀️" },
  { label: "储能功率", value: (kpi.value.bat_power_kw >= 0 ? "+" : "") + kpi.value.bat_power_kw.toFixed(1), unit: "kW", sub: `SOC ${kpi.value.soc.toFixed(0)}%`, icon: "🔋" },
  { label: "CHP 发电", value: (kpi.value.chp_power_kw || 0).toFixed(1), unit: "kW", icon: "🔥" },
  { label: "热泵供热", value: (kpi.value.hp_thermal_kw || 0).toFixed(1), unit: "kW", icon: "❄️" },
]);
</script>

<template>
  <div>
    <!-- Hero KPI Strip -->
    <section class="hero-strip">
      <div class="hero-content">
        <h1 class="hero-title">IES 控制终端</h1>
        <p class="hero-sub">综合能源系统运行驾驶舱</p>
        <div class="kpi-row">
          <div v-for="c in kpiCards" :key="c.label" class="kpi-card-apple">
            <span class="kpi-icon-apple">{{ c.icon }}</span>
            <div>
              <div class="kpi-value-apple">{{ c.value }}<span class="kpi-unit-apple"> {{ c.unit }}</span></div>
              <div class="kpi-label-apple">{{ c.label }}<span v-if="c.sub" class="kpi-sub"> · {{ c.sub }}</span></div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Sankey + Bar -->
    <section class="page">
      <div class="chart-grid">
        <div class="chart-card">
          <h3 class="chart-title">能量流向</h3>
          <VChart :option="sankeyOption" style="height: 340px" autoresize />
        </div>
        <div class="chart-card">
          <h3 class="chart-title">设备功率</h3>
          <VChart :option="deviceBarOption" style="height: 340px" autoresize />
        </div>
      </div>
    </section>

    <!-- Device Status Quick View -->
    <section class="page">
      <h2 style="margin-bottom:24px">设备状态</h2>
      <div class="device-strip">
        <div v-for="d in devices" :key="d.device_id" class="device-chip" :class="{ warn: d.status === 0, ok: d.status > 0 }">
          <div class="chip-dot" :class="{ on: d.status > 0 }"></div>
          <div class="chip-info">
            <div class="chip-name">{{ d.name }}</div>
            <div class="chip-power">{{ Math.abs(d.power_kw).toFixed(1) }} kW</div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* ── Hero ─────────────────────────────────── */
.hero-strip {
  background: var(--apple-black);
  padding: 80px 24px 64px;
}
.hero-content {
  max-width: var(--content-max);
  margin: 0 auto;
}
.hero-title {
  font-family: var(--font-display);
  font-size: 56px;
  font-weight: 600;
  line-height: 1.07;
  letter-spacing: -0.28px;
  color: #ffffff;
}
.hero-sub {
  font-family: var(--font-display);
  font-size: 21px;
  font-weight: 400;
  line-height: 1.19;
  letter-spacing: 0.231px;
  color: rgba(255,255,255,0.72);
  margin-top: 8px;
}

.kpi-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-top: 48px;
}
.kpi-card-apple {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 20px;
  background: rgba(255,255,255,0.06);
  border-radius: 10px;
  backdrop-filter: blur(10px);
}
.kpi-icon-apple { font-size: 28px; }
.kpi-value-apple {
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 600;
  line-height: 1.14;
  color: #ffffff;
}
.kpi-unit-apple { font-size: 14px; font-weight: 400; color: rgba(255,255,255,0.56); }
.kpi-label-apple { font-size: 13px; font-weight: 400; color: rgba(255,255,255,0.64); margin-top: 2px; }
.kpi-sub { color: rgba(255,255,255,0.48); }

/* ── Charts ────────────────────────────────── */
.chart-grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 20px;
}
.chart-card {
  background: var(--surface-card);
  border-radius: 12px;
  padding: 24px;
  box-shadow: var(--shadow-subtle);
}
.chart-title {
  font-family: var(--font-display);
  font-size: 17px;
  font-weight: 600;
  line-height: 1.24;
  letter-spacing: -0.374px;
  margin-bottom: 16px;
}

/* ── Device chips ─────────────────────────── */
.device-strip {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}
.device-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  background: var(--surface-card);
  border-radius: 10px;
  box-shadow: var(--shadow-subtle);
}
.chip-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: rgba(0,0,0,0.15);
  flex-shrink: 0;
}
.chip-dot.on { background: var(--color-success); }
.chip-name {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: -0.224px;
  color: var(--apple-text);
}
.chip-power {
  font-size: 12px;
  color: var(--apple-text-secondary);
}

@media (max-width: 834px) {
  .hero-title { font-size: 36px; }
  .kpi-row { grid-template-columns: repeat(2, 1fr); }
  .chart-grid { grid-template-columns: 1fr; }
  .device-strip { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 480px) {
  .hero-strip { padding: 48px 16px 40px; }
  .hero-title { font-size: 28px; letter-spacing: 0.196px; }
  .kpi-row { grid-template-columns: 1fr; }
}
</style>
