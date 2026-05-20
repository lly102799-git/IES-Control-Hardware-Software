<script setup lang="ts">
import { ref, computed } from "vue";

const API_BASE = "http://localhost:8000";

interface Device { device_id: string; name: string; dev_type: string; }
const devices = ref<Device[]>([]);
const selectedDevice = ref("");
const reportType = ref<"daily" | "monthly" | "export">("daily");
const date = ref(new Date().toISOString().slice(0, 10));
const month = ref(new Date().toISOString().slice(0, 7));
const pointName = ref("active_power");
const loading = ref(false);
const reportData = ref<any>(null);
const exportUrl = ref("");

const pointOptions = computed(() => {
  const t = devices.value.find(d => d.device_id === selectedDevice.value)?.dev_type;
  if (!t) return ["active_power"];
  if (t === "pv_inverter") return ["active_power","dc_voltage","dc_current","daily_energy","temp_module"];
  if (t === "battery_pcs") return ["active_power","dc_voltage","soc","temp_battery"];
  if (t === "chp") return ["active_power","heat_power","gas_flow"];
  if (t === "heatpump") return ["elec_power","thermal_power","cop"];
  if (t === "thermal_storage") return ["power","heat_soc","cool_soc","tank_temp"];
  if (t === "smart_meter") return ["active_power","voltage","current","total_energy"];
  if (t === "env_sensor") return ["temperature","humidity","co2","pm25"];
  if (t === "pipe_sensor") return ["temp_supply","temp_return","flow_rate"];
  return ["active_power"];
});

async function fetchDevices() {
  const r = await fetch(`${API_BASE}/api/devices`).then(r => r.json());
  devices.value = r;
  if (r.length > 0) selectedDevice.value = r[0].device_id;
}

async function runReport() {
  loading.value = true; exportUrl.value = ""; reportData.value = null;
  try {
    if (reportType.value === "daily") {
      const r = await fetch(`${API_BASE}/api/report/daily?device_id=${selectedDevice.value}&date=${date.value}`);
      reportData.value = await r.json();
    } else if (reportType.value === "monthly") {
      const [y, m] = month.value.split("-");
      const r = await fetch(`${API_BASE}/api/report/monthly?device_id=${selectedDevice.value}&year=${y}&month=${parseInt(m)}`);
      reportData.value = await r.json();
    } else {
      const end = new Date().toISOString().slice(0, 10);
      const start = new Date(Date.now() - 7*86400000).toISOString().slice(0, 10);
      exportUrl.value = `${API_BASE}/api/report/export?device_id=${selectedDevice.value}&start=${start}&end=${end}&point_name=${pointName.value}`;
    }
  } catch (e) { console.error(e); }
  finally { loading.value = false; }
}

fetchDevices();
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1>数据报表</h1>
      <p>运行数据统计与 CSV 导出</p>
    </div>

    <!-- 查询条件 -->
    <el-card style="margin-bottom: 20px">
      <div style="display:flex; gap: 12px; align-items: center; flex-wrap: wrap">
        <el-select v-model="selectedDevice" style="width: 180px">
          <el-option v-for="d in devices" :key="d.device_id" :label="d.name" :value="d.device_id" />
        </el-select>
        <el-radio-group v-model="reportType">
          <el-radio-button value="daily">日报</el-radio-button>
          <el-radio-button value="monthly">月报</el-radio-button>
          <el-radio-button value="export">CSV 导出</el-radio-button>
        </el-radio-group>
        <el-date-picker v-if="reportType === 'daily'" v-model="date" type="date" placeholder="选择日期" style="width:160px" />
        <el-date-picker v-if="reportType === 'monthly'" v-model="month" type="month" placeholder="选择月份" style="width:160px" />
        <el-select v-if="reportType === 'export'" v-model="pointName" style="width:140px">
          <el-option v-for="p in pointOptions" :key="p" :label="p" :value="p" />
        </el-select>
        <el-button type="primary" @click="runReport" :loading="loading">
          {{ reportType === 'export' ? '生成下载链接' : '查询' }}
        </el-button>
      </div>
    </el-card>

    <!-- 日报结果 -->
    <el-card v-if="reportData && reportType === 'daily'">
      <template #header>日报 — {{ selectedDevice }} · {{ date }}</template>
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="最小值">{{ (reportData.min ?? 0).toFixed(1) }} W</el-descriptions-item>
        <el-descriptions-item label="最大值">{{ (reportData.max ?? 0).toFixed(1) }} W</el-descriptions-item>
        <el-descriptions-item label="平均值">{{ (reportData.avg ?? 0).toFixed(1) }} W</el-descriptions-item>
        <el-descriptions-item label="日电量">{{ reportData.energy_kwh ?? '-' }} kWh</el-descriptions-item>
        <el-descriptions-item label="数据点数">{{ reportData.data_points ?? 0 }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 月报结果 -->
    <el-card v-if="reportData && reportType === 'monthly'">
      <template #header>月报 — {{ selectedDevice }} · {{ month }}</template>
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="平均功率">{{ (reportData.avg_power_w ?? 0).toFixed(1) }} W</el-descriptions-item>
        <el-descriptions-item label="最大功率">{{ (reportData.max_power_w ?? 0).toFixed(1) }} W</el-descriptions-item>
        <el-descriptions-item label="数据点数">{{ reportData.data_points ?? 0 }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- CSV 导出 -->
    <el-card v-if="exportUrl && reportType === 'export'">
      <template #header>CSV 导出 — {{ selectedDevice }} · {{ pointName }}</template>
      <p style="margin-bottom:12px;color:var(--apple-text-secondary)">近 7 天数据，点击下方按钮下载 CSV 文件。</p>
      <el-button type="primary">
        <a :href="exportUrl" download style="color:inherit;text-decoration:none">⬇ 下载 CSV</a>
      </el-button>
    </el-card>
  </div>
</template>
