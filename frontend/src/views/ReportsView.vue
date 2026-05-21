<script setup lang="ts">
import { ref, computed } from "vue";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { LineChart, BarChart, CandlestickChart } from "echarts/charts";
import { TitleComponent, TooltipComponent, GridComponent, LegendComponent, DataZoomComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

use([LineChart, BarChart, CandlestickChart, TitleComponent, TooltipComponent, GridComponent, LegendComponent, DataZoomComponent, CanvasRenderer]);

const API_BASE = "http://localhost:8000";

interface Device { device_id: string; name: string; dev_type: string; }
const devices = ref<Device[]>([]);
const selectedDevice = ref("");
const reportType = ref<"daily" | "monthly" | "export">("daily");
const date = ref(new Date().toISOString().slice(0, 10));
const month = ref(new Date().toISOString().slice(0, 7));
const exportStart = ref(new Date(Date.now() - 7*86400000).toISOString().slice(0, 10));
const exportEnd = ref(new Date().toISOString().slice(0, 10));
const pointName = ref("active_power");
const loading = ref(false);
const reportData = ref<any>(null);
const trendData = ref<any[]>([]);
const monthlyDaily = ref<any[]>([]);
const exportUrl = ref("");

const pointOptions = computed(() => {
  const t = devices.value.find(d => d.device_id === selectedDevice.value)?.dev_type;
  if (!t) return ["active_power"];
  const map: Record<string, string[]> = {
    pv_inverter: ["active_power","dc_voltage","dc_current","daily_energy","temp_module"],
    battery_pcs: ["active_power","dc_voltage","soc","temp_battery"],
    chp: ["active_power","heat_power","gas_flow"],
    heatpump: ["elec_power","thermal_power","cop"],
    thermal_storage: ["power","heat_soc","cool_soc","tank_temp"],
    smart_meter: ["active_power","voltage","current","total_energy"],
    env_sensor: ["temperature","humidity","co2","pm25"],
    pipe_sensor: ["temp_supply","temp_return","flow_rate"],
  };
  return map[t] || ["active_power"];
});

async function fetchDevices() {
  const r = await fetch(`${API_BASE}/api/devices`).then(r => r.json());
  devices.value = r;
  if (r.length > 0) selectedDevice.value = r[0].device_id;
}

async function runReport() {
  loading.value = true; exportUrl.value = ""; reportData.value = null; trendData.value = []; monthlyDaily.value = [];
  try {
    if (reportType.value === "daily") {
      const [rep, hist] = await Promise.all([
        fetch(`${API_BASE}/api/report/daily?device_id=${selectedDevice.value}&date=${date.value}&point_name=${pointName.value}`).then(r => r.json()),
        fetch(`${API_BASE}/api/devices/${selectedDevice.value}/history?point_name=${pointName.value}&start=${date.value}T00:00:00&end=${date.value}T23:59:59`).then(r => r.json()),
      ]);
      reportData.value = rep;
      trendData.value = (hist.data || []).map((d: any) => ({
        ts: new Date(d.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
        val: d.val,
      }));
    } else if (reportType.value === "monthly") {
      const [y, m] = month.value.split("-");
      const r = await fetch(`${API_BASE}/api/report/monthly?device_id=${selectedDevice.value}&year=${y}&month=${parseInt(m)}&point_name=${pointName.value}`);
      const d = await r.json();
      monthlyDaily.value = d.daily || [];
      reportData.value = d;
    } else {
      exportUrl.value = `${API_BASE}/api/report/export?device_id=${selectedDevice.value}&start=${exportStart.value}&end=${exportEnd.value}`;
    }
  } catch (e) { console.error(e); }
  finally { loading.value = false; }
}

fetchDevices();

// ── 日报趋势图 ───────────────────────────────────────────
const dailyChartOption = computed(() => ({
  title: { text: `${pointName.value} 当日趋势`, left: "center", textStyle: { fontSize: 14 } },
  tooltip: { trigger: "axis" },
  grid: { top: 50, bottom: 40, left: 60, right: 30 },
  xAxis: { type: "category", data: trendData.value.map((d: any) => d.ts), axisLabel: { interval: Math.floor(trendData.value.length / 8) } },
  yAxis: { type: "value" },
  dataZoom: [{ type: "inside" }, { type: "slider", bottom: 5 }],
  series: [{
    type: "line", data: trendData.value.map((d: any) => d.val),
    smooth: true, areaStyle: { opacity: 0.1 }, symbol: "none",
  }],
}));

// ── 月报逐日图 ───────────────────────────────────────────
const monthlyChartOption = computed(() => ({
  title: { text: `${pointName.value} 逐日统计`, left: "center", textStyle: { fontSize: 14 } },
  tooltip: { trigger: "axis" },
  legend: { data: ["最小值","平均值","最大值"], bottom: 0 },
  grid: { top: 50, bottom: 50, left: 60, right: 30 },
  xAxis: { type: "category", data: monthlyDaily.value.map((d: any) => d.date.slice(5)) },
  yAxis: { type: "value" },
  series: [
    { name: "最小值", type: "bar", data: monthlyDaily.value.map((d: any) => d.min), itemStyle: { color: "#67c23a", borderRadius: [3,3,0,0] }, barGap: "10%" },
    { name: "平均值", type: "bar", data: monthlyDaily.value.map((d: any) => d.avg), itemStyle: { color: "#409eff", borderRadius: [3,3,0,0] } },
    { name: "最大值", type: "bar", data: monthlyDaily.value.map((d: any) => d.max), itemStyle: { color: "#f56c6c", borderRadius: [3,3,0,0] } },
  ],
}));
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1>数据报表</h1>
      <p>运行数据统计与 CSV 导出</p>
    </div>

    <!-- 设备选择 -->
    <div style="margin-bottom: 20px; display: flex; align-items: center; gap: 12px">
      <label style="font-size:14px;font-weight:600;color:var(--apple-text)">设备：</label>
      <el-select v-model="selectedDevice" style="width: 220px">
        <el-option v-for="d in devices" :key="d.device_id" :label="d.name" :value="d.device_id" />
      </el-select>
    </div>

    <!-- 三栏报表卡片 -->
    <el-row :gutter="20" style="margin-bottom: 20px">
      <!-- 日报 -->
      <el-col :span="8">
        <el-card class="report-card" :class="{ active: reportType === 'daily' }" @click="reportType = 'daily'">
          <template #header><span>日报统计</span></template>
          <div style="display:flex;flex-direction:column;gap:10px">
            <el-date-picker v-model="date" type="date" style="width:100%" />
            <el-select v-model="pointName" style="width:100%">
              <el-option v-for="p in pointOptions" :key="p" :label="p" :value="p" />
            </el-select>
            <el-button type="primary" @click="runReport" :loading="loading && reportType === 'daily'" style="width:100%">
              查询日报
            </el-button>
          </div>
        </el-card>
      </el-col>

      <!-- 月报 -->
      <el-col :span="8">
        <el-card class="report-card" :class="{ active: reportType === 'monthly' }" @click="reportType = 'monthly'">
          <template #header><span>月报统计</span></template>
          <div style="display:flex;flex-direction:column;gap:10px">
            <el-date-picker v-model="month" type="month" style="width:100%" />
            <el-select v-model="pointName" style="width:100%">
              <el-option v-for="p in pointOptions" :key="p" :label="p" :value="p" />
            </el-select>
            <el-button type="primary" @click="runReport" :loading="loading && reportType === 'monthly'" style="width:100%">
              查询月报
            </el-button>
          </div>
        </el-card>
      </el-col>

      <!-- CSV 导出 -->
      <el-col :span="8">
        <el-card class="report-card" :class="{ active: reportType === 'export' }" @click="reportType = 'export'">
          <template #header><span>CSV 全量导出</span></template>
          <div style="display:flex;flex-direction:column;gap:10px">
            <div style="display:flex;gap:6px;align-items:center">
              <el-date-picker v-model="exportStart" type="date" style="flex:1" placeholder="起始" />
              <span style="color:var(--apple-text-tertiary);font-size:12px">至</span>
              <el-date-picker v-model="exportEnd" type="date" style="flex:1" placeholder="结束" />
            </div>
            <p style="font-size:12px;color:var(--apple-text-tertiary);margin:0">导出所有测点数据（最长31天）</p>
            <el-button type="primary" @click="runReport" :loading="loading && reportType === 'export'" style="width:100%">
              生成 CSV
            </el-button>
            <el-button v-if="exportUrl" type="success" style="width:100%">
              <a :href="exportUrl" download style="color:inherit;text-decoration:none">⬇ 下载 CSV 文件</a>
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>


    <!-- ═══ 日报 ═══ -->
    <template v-if="reportData && reportType === 'daily'">
      <el-card style="margin-bottom: 20px">
        <template #header>日报统计 — {{ pointName }} · {{ date }}</template>
        <el-descriptions :column="4" border size="small">
          <el-descriptions-item label="最小值">{{ (reportData.min ?? 0).toFixed(1) }}</el-descriptions-item>
          <el-descriptions-item label="最大值">{{ (reportData.max ?? 0).toFixed(1) }}</el-descriptions-item>
          <el-descriptions-item label="平均值">{{ (reportData.avg ?? 0).toFixed(1) }}</el-descriptions-item>
          <el-descriptions-item v-if="reportData.energy_kwh != null" label="日电量">{{ reportData.energy_kwh }} kWh</el-descriptions-item>
          <el-descriptions-item label="数据点数">{{ reportData.data_points ?? 0 }}</el-descriptions-item>
        </el-descriptions>
      </el-card>
      <el-card v-if="trendData.length">
        <VChart :option="dailyChartOption" style="height: 360px" autoresize />
      </el-card>
    </template>

    <!-- ═══ 月报 ═══ -->
    <template v-if="monthlyDaily.length && reportType === 'monthly'">
      <el-card style="margin-bottom: 20px">
        <VChart :option="monthlyChartOption" style="height: 380px" autoresize />
      </el-card>
      <el-card>
        <template #header>逐日明细 — {{ pointName }} · {{ month }}</template>
        <el-table :data="monthlyDaily" size="small" max-height="400">
          <el-table-column prop="date" label="日期" width="110" />
          <el-table-column label="最小值" width="110">
            <template #default="{ row }">{{ row.min?.toFixed(1) }}</template>
          </el-table-column>
          <el-table-column label="最大值" width="110">
            <template #default="{ row }">{{ row.max?.toFixed(1) }}</template>
          </el-table-column>
          <el-table-column label="平均值" width="110">
            <template #default="{ row }">{{ row.avg?.toFixed(1) }}</template>
          </el-table-column>
          <el-table-column prop="cnt" label="数据点" width="80" />
        </el-table>
      </el-card>
    </template>

  </div>
</template>

<style scoped>
.report-card {
  cursor: pointer;
  transition: box-shadow 0.2s;
  border: 2px solid transparent;
}
.report-card.active {
  border-color: var(--apple-blue);
  box-shadow: var(--shadow-card);
}
</style>
