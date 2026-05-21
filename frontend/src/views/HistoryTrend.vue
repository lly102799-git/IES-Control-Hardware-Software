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

const loading = ref(false);
const pointName = ref("active_power");
const historyData = ref<{ ts: string; val: number }[]>([]);

const pointOptions = [
  { label: "有功功率", value: "active_power" },
  { label: "DC 电压", value: "dc_voltage" },
  { label: "DC 电流", value: "dc_current" },
  { label: "AC 电压", value: "ac_voltage_a" },
  { label: "AC 电流", value: "ac_current_a" },
  ...(props.id === "battery_pcs_01"
    ? [{ label: "SOC", value: "soc" }]
    : [{ label: "组件温度", value: "temp_module" }]),
];

const deviceName = computed(() =>
  props.id === "pv_inverter_01" ? "光伏逆变器 #1" : "储能变流器 #1"
);

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
