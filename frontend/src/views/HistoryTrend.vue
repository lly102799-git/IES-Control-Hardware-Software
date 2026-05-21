<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import { useRouter } from "vue-router";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DataZoomComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

use([
  LineChart,
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DataZoomComponent,
  CanvasRenderer,
]);

const props = defineProps<{ id: string }>();
const router = useRouter();
import { API_BASE } from "../config";

// ── 各设备类型对应的遥测字段 ──────────────────────────
const POINT_LABELS: Record<string, string> = {
  active_power: "有功功率", reactive_power: "无功功率",
  dc_voltage: "DC 电压", dc_current: "DC 电流",
  ac_voltage_a: "AC 电压", ac_current_a: "AC 电流",
  pf: "功率因数", frequency: "频率",
  daily_energy: "日发电量", total_energy: "总发电量",
  temp_module: "组件温度", temp_cabinet: "机柜温度",
  soc: "SOC", soh: "SOH", temp_battery: "电池温度",
  heat_power: "热功率", gas_flow: "燃气流量",
  elec_efficiency: "发电效率", total_efficiency: "总效率",
  temp_out: "出水温度", temp_in: "进水温度",
  elec_power: "耗电功率", thermal_power: "制热功率",
  cop: "COP", mode: "运行模式",
  heat_stored: "蓄热量", power: "功率",
  heat_soc: "热 SOC", cool_soc: "冷 SOC", tank_temp: "罐温",
  voltage: "电压", current: "电流", power_factor: "功率因数",
  temperature: "温度", humidity: "湿度", co2: "CO₂", pm25: "PM2.5",
  temp_supply: "供水温度", temp_return: "回水温度", flow_rate: "流量",
  status: "运行状态",
};

const DEVICE_POINTS: Record<string, string[]> = {
  pv_inverter_01:    ["active_power","dc_voltage","dc_current","ac_voltage_a","ac_current_a","reactive_power","pf","frequency","daily_energy","total_energy","temp_module","temp_cabinet","status"],
  battery_pcs_01:    ["active_power","dc_voltage","dc_current","ac_voltage_a","ac_current_a","reactive_power","soc","soh","temp_battery","temp_cabinet","status"],
  chp_01:            ["active_power","heat_power","gas_flow","elec_efficiency","total_efficiency","temp_out","temp_in","status"],
  heatpump_01:       ["elec_power","thermal_power","cop","temp_out","temp_in","mode"],
  thermal_storage_01:["power","heat_stored","heat_soc","cool_soc","tank_temp","mode"],
  smart_meter_01:    ["active_power","voltage","current","power_factor","frequency","total_energy"],
  env_sensor_01:     ["temperature","humidity","co2","pm25"],
  pipe_sensor_01:    ["temp_supply","temp_return","flow_rate"],
};

const DEVICE_NAMES: Record<string, string> = {
  pv_inverter_01: "光伏逆变器 #1", battery_pcs_01: "储能变流器 #1",
  chp_01: "CHP 三联供 #1", heatpump_01: "热泵 #1",
  thermal_storage_01: "蓄能罐 #1", smart_meter_01: "智能电表 #1",
  env_sensor_01: "环境传感器 #1", pipe_sensor_01: "管道传感器 #1",
};

const loading = ref(false);
const pointOptions = DEVICE_POINTS[props.id]?.map((v) => ({
  label: POINT_LABELS[v] || v, value: v,
})) || [];
const pointName = ref(pointOptions[0]?.value || "");
const historyData = ref<{ ts: string; val: number }[]>([]);
const deviceName = DEVICE_NAMES[props.id] || props.id;

async function fetchHistory() {
  loading.value = true;
  try {
    const now = new Date();
    const start = new Date(now.getTime() - 2 * 60 * 60 * 1000); // 最近 2 小时
    const res = await fetch(
      `${API_BASE}/api/devices/${props.id}/history?` +
        `point_name=${pointName.value}&` +
        `start=${start.toISOString()}&` +
        `end=${now.toISOString()}`
    );
    const json = await res.json();
    historyData.value = (json.data || []).map((d: any) => ({
      ts: new Date(d.ts).toLocaleTimeString(),
      val: d.val,
    }));
  } catch (e) {
    console.error(e);
  } finally {
    loading.value = false;
  }
}

onMounted(fetchHistory);

const chartOption = computed(() => ({
  title: {
    text: pointOptions.find((p) => p.value === pointName.value)?.label || "",
    left: "center",
    textStyle: { fontSize: 14 },
  },
  tooltip: { trigger: "axis" },
  xAxis: { type: "category", data: historyData.value.map((d) => d.ts) },
  yAxis: { type: "value" },
  dataZoom: [{ type: "inside" }, { type: "slider" }],
  series: [
    {
      type: "line",
      data: historyData.value.map((d) => d.val),
      smooth: true,
      areaStyle: { opacity: 0.1 },
    },
  ],
}));
</script>

<template>
  <div class="page">
    <div class="page-header">
      <el-button @click="router.back()" text>← 返回</el-button>
      <h1>{{ deviceName }} · 历史趋势</h1>
      <div class="controls">
        <el-select v-model="pointName" @change="fetchHistory" style="width: 160px">
          <el-option
            v-for="opt in pointOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-button @click="fetchHistory" type="primary" :loading="loading">
          刷新
        </el-button>
      </div>
    </div>

    <el-card shadow="hover" v-loading="loading">
      <VChart :option="chartOption" style="height: 400px" autoresize />
    </el-card>

    <el-empty v-if="!loading && historyData.length === 0" description="暂无历史数据" />
  </div>
</template>

<style scoped>
.page-header { display: flex; align-items: center; gap: 16px; }
.page-header h1 { flex: 1; }
.controls { display: flex; gap: 8px; }
</style>
