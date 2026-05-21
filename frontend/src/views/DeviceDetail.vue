<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from "vue";
import { useRouter } from "vue-router";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { LineChart, GaugeChart } from "echarts/charts";
import { TitleComponent, TooltipComponent, GridComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

use([LineChart, GaugeChart, TitleComponent, TooltipComponent, GridComponent, LegendComponent, CanvasRenderer]);

const props = defineProps<{ id: string }>();
const router = useRouter();
import { API_BASE, WS_URL } from "../config";
const realtime = ref<Record<string, number>>({});
let ws: WebSocket | null = null;

// ── 设备元数据（从 API 获取）────────────────────────────

interface DeviceMeta { name: string; dev_type: string; rated_power_w: number; capacity_kwh?: number }
const deviceMeta = ref<DeviceMeta>({ name: "加载中...", dev_type: "", rated_power_w: 0 });

async function fetchMeta() {
  try {
    const r = await fetch(`${API_BASE}/api/devices`);
    const list = await r.json();
    const d = list.find((d: any) => d.device_id === props.id);
    if (d) deviceMeta.value = d;
  } catch {}
}

// ── 遥测字段定义（按设备类型）───────────────────────────

interface FieldDef { key: string; label: string; unit: string; precision: number }
const fieldDefs = computed<FieldDef[]>(() => {
  const t = deviceMeta.value.dev_type;
  if (t === "pv_inverter") return [
    { key: "dc_voltage", label: "DC 电压", unit: "V", precision: 1 },
    { key: "dc_current", label: "DC 电流", unit: "A", precision: 2 },
    { key: "ac_voltage_a", label: "AC 电压", unit: "V", precision: 1 },
    { key: "ac_current_a", label: "AC 电流", unit: "A", precision: 2 },
    { key: "active_power", label: "有功功率", unit: "kW", precision: 2, scale: 0.001 },
    { key: "pf", label: "功率因数", unit: "", precision: 2 },
    { key: "frequency", label: "频率", unit: "Hz", precision: 1 },
    { key: "daily_energy", label: "当日发电", unit: "kWh", precision: 1 },
    { key: "temp_module", label: "组件温度", unit: "°C", precision: 1 },
    { key: "temp_cabinet", label: "机柜温度", unit: "°C", precision: 1 },
  ];
  if (t === "battery_pcs") return [
    { key: "dc_voltage", label: "DC 电压", unit: "V", precision: 1 },
    { key: "dc_current", label: "DC 电流", unit: "A", precision: 2 },
    { key: "ac_voltage_a", label: "AC 电压", unit: "V", precision: 1 },
    { key: "ac_current_a", label: "AC 电流", unit: "A", precision: 2 },
    { key: "active_power", label: "有功功率", unit: "kW", precision: 2, scale: 0.001 },
    { key: "soc", label: "SOC", unit: "%", precision: 1 },
    { key: "soh", label: "SOH", unit: "%", precision: 1 },
    { key: "temp_battery", label: "电池温度", unit: "°C", precision: 1 },
    { key: "temp_cabinet", label: "机柜温度", unit: "°C", precision: 1 },
  ];
  if (t === "chp") return [
    { key: "active_power", label: "发电功率", unit: "kW", precision: 2, scale: 0.001 },
    { key: "heat_power", label: "余热功率", unit: "kW", precision: 2, scale: 0.001 },
    { key: "gas_flow", label: "燃气流量", unit: "Nm³/h", precision: 2 },
    { key: "elec_efficiency", label: "发电效率", unit: "%", precision: 1 },
    { key: "total_efficiency", label: "综合效率", unit: "%", precision: 1 },
    { key: "temp_out", label: "出水温度", unit: "°C", precision: 1 },
    { key: "temp_in", label: "回水温度", unit: "°C", precision: 1 },
  ];
  if (t === "heatpump") return [
    { key: "elec_power", label: "耗电功率", unit: "kW", precision: 2, scale: 0.001 },
    { key: "thermal_power", label: "制冷/热量", unit: "kW", precision: 2, scale: 0.001 },
    { key: "cop", label: "COP", unit: "", precision: 1 },
    { key: "temp_out", label: "出水温度", unit: "°C", precision: 1 },
    { key: "temp_in", label: "回水温度", unit: "°C", precision: 1 },
  ];
  if (t === "thermal_storage") return [
    { key: "heat_stored", label: "蓄热量", unit: "kWh", precision: 1 },
    { key: "power", label: "蓄/放功率", unit: "kW", precision: 2, scale: 0.001 },
    { key: "heat_soc", label: "储热 SOC", unit: "%", precision: 1 },
    { key: "cool_soc", label: "储冷 SOC", unit: "%", precision: 1 },
    { key: "tank_temp", label: "罐温", unit: "°C", precision: 1 },
  ];
  if (t === "smart_meter") return [
    { key: "active_power", label: "实时功率", unit: "W", precision: 1 },
    { key: "voltage", label: "电压", unit: "V", precision: 1 },
    { key: "current", label: "电流", unit: "A", precision: 2 },
    { key: "power_factor", label: "功率因数", unit: "", precision: 2 },
    { key: "frequency", label: "频率", unit: "Hz", precision: 2 },
    { key: "total_energy", label: "累计电量", unit: "kWh", precision: 1 },
  ];
  if (t === "env_sensor") return [
    { key: "temperature", label: "温度", unit: "°C", precision: 1 },
    { key: "humidity", label: "湿度", unit: "%", precision: 1 },
    { key: "co2", label: "CO₂", unit: "ppm", precision: 1 },
    { key: "pm25", label: "PM2.5", unit: "μg/m³", precision: 1 },
  ];
  if (t === "pipe_sensor") return [
    { key: "temp_supply", label: "供水温度", unit: "°C", precision: 1 },
    { key: "temp_return", label: "回水温度", unit: "°C", precision: 1 },
    { key: "flow_rate", label: "流量", unit: "m³/h", precision: 1 },
  ];
  return [];
});

function fmtField(f: FieldDef): string {
  let v = realtime.value[f.key];
  if (v == null) return "-";
  if ((f as any).scale) v = v * (f as any).scale;
  return v.toFixed(f.precision) + (f.unit ? " " + f.unit : "");
}

// ── WebSocket ────────────────────────────────────────────

function connectWs() {
  ws = new WebSocket(WS_URL);
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "realtime" && msg.data[props.id]) {
      realtime.value = msg.data[props.id];
    }
  };
  ws.onclose = () => setTimeout(connectWs, 3000);
}

async function fetchRealtime() {
  try {
    const res = await fetch(`${API_BASE}/api/devices/${props.id}/realtime`);
    const json = await res.json();
    realtime.value = json.data || {};
  } catch {}
}

onMounted(() => { fetchMeta(); connectWs(); fetchRealtime(); });
onUnmounted(() => ws?.close());

// ── 图表 ─────────────────────────────────────────────────

const powerField = computed(() => {
  const t = deviceMeta.value.dev_type;
  if (t === "pv_inverter" || t === "battery_pcs") return "active_power";
  if (t === "chp") return "active_power";
  if (t === "heatpump") return "elec_power";
  if (t === "thermal_storage") return "power";
  if (t === "smart_meter") return "active_power";
  if (t === "env_sensor") return "temperature";
  if (t === "pipe_sensor") return "temp_supply";
  return "active_power";
});

const gaugeTitle = computed(() => {
  const t = deviceMeta.value.dev_type;
  if (t === "battery_pcs") return "SOC";
  if (t === "thermal_storage") return "储热 SOC";
  if (t === "heatpump") return "COP";
  if (t === "chp") return "综合效率";
  if (t === "smart_meter") return "电压";
  if (t === "env_sensor") return "温度";
  if (t === "pipe_sensor") return "供水温度";
  return "实时功率";
});

const gaugeData = computed(() => {
  const t = deviceMeta.value.dev_type;
  const rt = realtime.value;
  if (t === "battery_pcs") return { value: rt.soc ?? 0, unit: "%", max: 100 };
  if (t === "thermal_storage") return { value: rt.heat_soc ?? 0, unit: "%", max: 100 };
  if (t === "heatpump") return { value: rt.cop ?? 0, unit: "", max: 6 };
  if (t === "chp") return { value: (rt.total_efficiency ?? 0), unit: "%", max: 100 };
  if (t === "smart_meter") return { value: rt.voltage ?? 0, unit: "V", max: 260 };
  if (t === "env_sensor") return { value: rt.temperature ?? 0, unit: "°C", max: 50 };
  if (t === "pipe_sensor") return { value: rt.temp_supply ?? 0, unit: "°C", max: 60 };
  const pw = (rt[powerField.value] ?? 0) / 1000;
  return { value: pw, unit: "kW", max: deviceMeta.value.rated_power_w / 1000 };
});

const gaugeOption = computed(() => ({
  title: { text: gaugeTitle.value, left: "center", textStyle: { fontSize: 14 } },
  series: [{
    type: "gauge", startAngle: 200, endAngle: -20,
    min: 0, max: gaugeData.value.max,
    data: [{ value: +gaugeData.value.value.toFixed(1), name: gaugeData.value.unit }],
    detail: { formatter: `{value}${gaugeData.value.unit}` },
  }],
}));

const powerChartOption = computed(() => ({
  title: { text: "实时功率趋势", left: "center", textStyle: { fontSize: 14 } },
  tooltip: { trigger: "axis" },
  xAxis: { type: "category", show: false },
  yAxis: { type: "value", name: "kW" },
  series: [{
    type: "line",
    data: [(realtime.value[powerField.value] ?? 0) / 1000],
    smooth: true, areaStyle: { opacity: 0.15 },
  }],
}));
</script>

<template>
  <div class="page">
    <div class="page-header">
      <el-button @click="router.back()" text>← 返回</el-button>
      <h1>{{ deviceMeta.name }}</h1>
      <el-button @click="router.push(`/history/${id}`)" type="primary" plain>历史趋势</el-button>
    </div>

    <el-row :gutter="20">
      <el-col :span="8">
        <el-card shadow="hover">
          <VChart :option="gaugeOption" style="height: 280px" autoresize />
        </el-card>
      </el-col>
      <el-col :span="16">
        <el-card shadow="hover">
          <VChart :option="powerChartOption" style="height: 280px" autoresize />
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="hover" style="margin-top: 20px">
      <template #header><span>实时遥测数据</span></template>
      <el-descriptions :column="4" border size="small">
        <el-descriptions-item
          v-for="f in fieldDefs"
          :key="f.key"
          :label="f.label"
        >{{ fmtField(f) }}</el-descriptions-item>
      </el-descriptions>
      <el-empty v-if="fieldDefs.length === 0" description="暂无遥测数据" />
    </el-card>
  </div>
</template>

<style scoped>
.page { padding: 24px; max-width: 1400px; margin: 0 auto; }
.page-header { display: flex; align-items: center; gap: 16px; margin-bottom: 24px; }
.page-header h1 { flex: 1; font-size: 24px; margin: 0; }
</style>
