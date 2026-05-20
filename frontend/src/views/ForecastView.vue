<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  TitleComponent, TooltipComponent, GridComponent,
  LegendComponent, DataZoomComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

use([LineChart, TitleComponent, TooltipComponent, GridComponent,
     LegendComponent, DataZoomComponent, CanvasRenderer]);

const API_BASE = "http://localhost:8000";

const rules = ref<any[]>([]);
const executions = ref<any[]>([]);
const pvForecast = ref<any[]>([]);
const pvActual = ref<any[]>([]);
const loadingPv = ref(false);
const loadingRules = ref(false);

async function fetchRules() {
  loadingRules.value = true;
  try {
    const [r, e] = await Promise.all([
      fetch(`${API_BASE}/api/rules`).then(r => r.json()),
      fetch(`${API_BASE}/api/rules/executions?limit=20`).then(r => r.json()),
    ]);
    rules.value = r.rules || [];
    executions.value = e.executions || [];
  } catch (err) { console.error(err); }
  finally { loadingRules.value = false; }
}

async function fetchForecast() {
  loadingPv.value = true;
  try {
    const f = await fetch(`${API_BASE}/api/forecast/pv?hours=24`).then(r => r.json());
    pvForecast.value = (f.forecast || []).map((d: any) => ({
      ts: new Date(d.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
      power: d.power_kw,
      ghi: d.ghi,
    }));
    // 获取今日实际发电作为对比
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
    const a = await fetch(
      `${API_BASE}/api/devices/pv_inverter_01/history?point_name=active_power&start=${start}&end=${now.toISOString()}`
    ).then(r => r.json());
    pvActual.value = (a.data || []).map((d: any) => ({
      ts: new Date(d.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
      power: d.val / 1000,
    }));
  } catch (err) { console.error(err); }
  finally { loadingPv.value = false; }
}

async function triggerForecast() {
  await fetch(`${API_BASE}/api/forecast/trigger`, { method: "POST" });
  setTimeout(() => fetchForecast(), 2000);
}

onMounted(() => {
  fetchRules();
  fetchForecast();
});

const pvChartOption = computed(() => ({
  title: { text: "光伏功率预测 vs 实际", left: "center", textStyle: { fontSize: 14 } },
  tooltip: { trigger: "axis" },
  legend: { data: ["预测功率(kW)", "实际功率(kW)", "GHI(W/m²)"], bottom: 0 },
  grid: { top: 50, bottom: 50, left: 60, right: 60 },
  xAxis: { type: "category", data: pvForecast.value.map((d: any) => d.ts) },
  yAxis: [
    { type: "value", name: "kW" },
    { type: "value", name: "W/m²" },
  ],
  dataZoom: [{ type: "slider", bottom: 25 }],
  series: [
    {
      name: "预测功率(kW)", type: "line",
      data: pvForecast.value.map((d: any) => d.power),
      smooth: true, lineStyle: { color: "#409eff" },
    },
    {
      name: "实际功率(kW)", type: "line",
      data: pvActual.value.map((d: any) => d.power),
      smooth: true, lineStyle: { color: "#67c23a" },
    },
    {
      name: "GHI(W/m²)", type: "line", yAxisIndex: 1,
      data: pvForecast.value.map((d: any) => d.ghi),
      smooth: true, lineStyle: { color: "#e6a23c", type: "dashed" },
    },
  ],
}));

function formatTime(ts: string) {
  return ts ? new Date(ts).toLocaleTimeString("zh-CN", { hour12: false }) : "-";
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1>预测调度</h1>
    </div>

    <!-- 预测曲线 -->
    <el-card shadow="hover" style="margin-bottom: 20px" v-loading="loadingPv">
      <template #header>
        <div class="card-title-row">
          <span>光伏功率预测 (晴空模型)</span>
          <el-button size="small" @click="triggerForecast">重新预测</el-button>
        </div>
      </template>
      <VChart :option="pvChartOption" style="height: 360px" autoresize />
    </el-card>

    <!-- 规则引擎状态 -->
    <el-row :gutter="20">
      <el-col :span="12">
        <el-card shadow="hover" v-loading="loadingRules">
          <template #header>
            <div class="card-title-row">
              <span>规则库 ({{ rules.length }} 条)</span>
              <el-button size="small" @click="fetchRules">刷新</el-button>
            </div>
          </template>
          <el-table :data="rules" size="small" max-height="300">
            <el-table-column prop="name" label="规则" width="120" />
            <el-table-column prop="description" label="说明" min-width="140" show-overflow-tooltip />
            <el-table-column label="优先级" width="70">
              <template #default="{ row }">{{ row.priority }}</template>
            </el-table-column>
            <el-table-column label="启用" width="60">
              <template #default="{ row }">
                <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
                  {{ row.enabled ? "开" : "关" }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <!-- 执行日志 -->
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header><span>规则执行日志</span></template>
          <el-table :data="executions" size="small" max-height="300" empty-text="暂无记录">
            <el-table-column label="时间" width="85">
              <template #default="{ row }">{{ formatTime(row.ts) }}</template>
            </el-table-column>
            <el-table-column prop="rule_name" label="规则" width="100" />
            <el-table-column label="模式" width="60">
              <template #default="{ row }">
                <el-tag :type="row.action_mode === 3 ? 'danger' : row.action_mode === 1 ? 'warning' : row.action_mode === 2 ? 'success' : 'info'" size="small">
                  {{ row.action_mode }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="condition_snapshot" label="状态快照" width="130" />
            <el-table-column label="结果" width="60">
              <template #default="{ row }">
                <el-tag :type="row.executed ? 'success' : 'danger'" size="small">
                  {{ row.executed ? "√" : "×" }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.card-title-row { display: flex; justify-content: space-between; align-items: center; }
</style>
