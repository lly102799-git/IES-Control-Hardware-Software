<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted } from "vue";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
import {
  TitleComponent, TooltipComponent, GridComponent,
  LegendComponent, DataZoomComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { API_BASE } from "../config";

use([LineChart, BarChart, TitleComponent, TooltipComponent, GridComponent,
     LegendComponent, DataZoomComponent, CanvasRenderer]);

// ═══════════════════════════════════════════════════════════
// 类型定义
// ═══════════════════════════════════════════════════════════

interface Device {
  device_id: string; name: string; dev_type: string; rated_power_w: number;
}
interface CmdRecord {
  ts: string; cmd_register: number; cmd_values: string;
  cmd_mode: number; cmd_duration: number; success: boolean; message: string;
}
interface ScheduleStep {
  step: number; minute: number;
  P_pv_kw: number; P_bat_ch_kw: number; P_bat_dis_kw: number;
  P_chp_kw: number; P_hp_kw: number;
  P_grid_import_kw: number; P_grid_export_kw: number;
  Q_ts_ch_kw: number; Q_ts_dis_kw: number;
  SOC_bat: number; SOC_ts: number; u_chp: number;
}
interface MilpStatus {
  status: string; total_cost_yuan: number; last_solve_ts: string;
  running: boolean; has_schedule: boolean;
  step_minutes: number; num_steps: number;
}

// ═══════════════════════════════════════════════════════════
// 状态
// ═══════════════════════════════════════════════════════════

const activeTab = ref<"milp" | "manual">("milp");
const schedule = ref<ScheduleStep[]>([]);
const milpStatus = ref<MilpStatus | null>(null);
const overrides = ref<Record<string, number>>({});
let refreshTimer: number | null = null;

// 手动控制 (保留原有逻辑)
const devices = ref<Device[]>([]);
const selectedDevice = ref("");
const cmdMode = ref(0);
const powerSetpoint = ref<number | null>(null);
const duration = ref(0);
const sending = ref(false);
const lastResult = ref<{ success: boolean; message: string } | null>(null);
const cmdHistory = ref<CmdRecord[]>([]);

// ═══════════════════════════════════════════════════════════
// MILP 数据加载
// ═══════════════════════════════════════════════════════════

interface ExecLog {
  setpoints: { ts: string; battery_kw: number; chp_kw: number; hp_kw: number;
               ts_kw: number; grid_net_kw: number; pv_kw: number; soc_bat: number; soc_ts: number }[];
  telemetry: Record<string, { ts: string; val: number }[]>;
}
const execLog = ref<ExecLog>({ setpoints: [], telemetry: {} });

async function fetchMilpData() {
  try {
    const [sRes, stRes, ovRes, exRes] = await Promise.all([
      fetch(`${API_BASE}/api/milp/schedule`),
      fetch(`${API_BASE}/api/milp/status`),
      fetch(`${API_BASE}/api/milp/overrides`),
      fetch(`${API_BASE}/api/milp/execution-log?minutes=40`),
    ]);
    const sData = await sRes.json();
    schedule.value = sData.schedule || [];
    milpStatus.value = await stRes.json();
    try { overrides.value = await ovRes.json(); } catch { overrides.value = {}; }
    try { execLog.value = await exRes.json(); } catch { execLog.value = { setpoints: [], telemetry: {} }; }
  } catch { /* silent */ }
}

// ═══════════════════════════════════════════════════════════
// 执行对比图：左侧=过去(实线实际+虚线指令) | 右侧=未来计划
// ═══════════════════════════════════════════════════════════

const powerChartOption = computed(() => {
  const steps = schedule.value;
  const sps = execLog.value.setpoints || [];
  const tm = execLog.value.telemetry || {};

  if (steps.length === 0 && sps.length === 0) return {};

  // ── 时间轴构建 ──
  // 左侧 (past): sps.length 个 setpoint 时间点 (每个 5min)
  // 右侧 (future): 16 个未来计划步 (80min)
  const PAST_STEPS = sps.length;
  const FUTURE_STEPS = Math.min(16, steps.length);
  const totalSteps = PAST_STEPS + FUTURE_STEPS;

  // 实际遥测 (后端已转换为 kW)
  const batActual = (tm["battery_pcs_01"] || []).map((p: any) => p.val);
  const pvActual = (tm["pv_inverter_01"] || []).map((p: any) => p.val);
  const gridActual = (tm["smart_meter_01"] || []).map((p: any) => p.val);
  const chpActual = (tm["chp_01"] || []).map((p: any) => p.val);

  // ── 系列数据 ──
  // 储能: 左侧用实际值, 左侧+右侧 setpoint 虚线
  const batPlan: (number | null)[] = [];
  const batActualSeries: (number | null)[] = [];

  for (let i = 0; i < totalSteps; i++) {
    if (i < PAST_STEPS) {
      // 过去: setpoint 来自执行日志
      batPlan.push(sps[i]?.battery_kw ?? null);
      // 实际值取对应时间窗口的均值
      const chunkSize = Math.max(1, Math.floor(batActual.length / PAST_STEPS));
      const start = i * chunkSize;
      const chunk = batActual.slice(start, start + chunkSize);
      batActualSeries.push(chunk.length > 0 ? chunk.reduce((a: number, b: number) => a + b, 0) / chunk.length : null);
    } else {
      // 未来: setpoint 来自调度方案
      const fi = i - PAST_STEPS;
      if (fi < FUTURE_STEPS && fi < steps.length) {
        batPlan.push(steps[fi].P_bat_dis_kw - steps[fi].P_bat_ch_kw);
      } else {
        batPlan.push(null);
      }
      batActualSeries.push(null); // 未来无实际值
    }
  }

  // 电网净功率
  const gridPlan: (number | null)[] = [];
  const gridActualSeries: (number | null)[] = [];
  for (let i = 0; i < totalSteps; i++) {
    if (i < PAST_STEPS) {
      gridPlan.push(sps[i]?.grid_net_kw ?? null);
      const cs = Math.max(1, Math.floor(gridActual.length / PAST_STEPS));
      const chunk = gridActual.slice(i * cs, (i + 1) * cs);
      gridActualSeries.push(chunk.length > 0 ? chunk.reduce((a: number, b: number) => a + b, 0) / chunk.length : null);
    } else {
      const fi = i - PAST_STEPS;
      gridPlan.push(fi < FUTURE_STEPS && fi < steps.length ? steps[fi].P_grid_import_kw - steps[fi].P_grid_export_kw : null);
      gridActualSeries.push(null);
    }
  }

  // PV
  const pvPlan: (number | null)[] = [];
  const pvActualSeries: (number | null)[] = [];
  for (let i = 0; i < totalSteps; i++) {
    if (i < PAST_STEPS) {
      pvPlan.push(sps[i]?.pv_kw ?? null);
      const cs = Math.max(1, Math.floor(pvActual.length / PAST_STEPS));
      const chunk = pvActual.slice(i * cs, (i + 1) * cs);
      pvActualSeries.push(chunk.length > 0 ? chunk.reduce((a: number, b: number) => a + b, 0) / chunk.length : null);
    } else {
      const fi = i - PAST_STEPS;
      pvPlan.push(fi < FUTURE_STEPS && fi < steps.length ? steps[fi].P_pv_kw : null);
      pvActualSeries.push(null);
    }
  }

  // CHP
  const chpPlan: (number | null)[] = [];
  const chpActualSeries: (number | null)[] = [];
  for (let i = 0; i < totalSteps; i++) {
    if (i < PAST_STEPS) {
      chpPlan.push(sps[i]?.chp_kw ?? null);
      const cs = Math.max(1, Math.floor(chpActual.length / PAST_STEPS));
      const chunk = chpActual.slice(i * cs, (i + 1) * cs);
      chpActualSeries.push(chunk.length > 0 ? chunk.reduce((a: number, b: number) => a + b, 0) / chunk.length : null);
    } else {
      const fi = i - PAST_STEPS;
      chpPlan.push(fi < FUTURE_STEPS && fi < steps.length ? steps[fi].P_chp_kw : null);
      chpActualSeries.push(null);
    }
  }

  // ── X 轴标签 ──
  const labels: string[] = [];
  for (let i = 0; i < totalSteps; i++) {
    if (i < PAST_STEPS) {
      labels.push(`-${(PAST_STEPS - i) * 5}min`);
    } else {
      labels.push(`+${(i - PAST_STEPS + 1) * 5}min`);
    }
  }

  // ── markLine 分隔 past/future ──
  const nowMark = PAST_STEPS > 0 ? PAST_STEPS - 0.5 : 0;

  return {
    title: { text: "执行对比 · 调度计划", left: "center", textStyle: { fontSize: 13, color: "#1d1d1f" } },
    tooltip: { trigger: "axis" },
    legend: {
      bottom: 0,
      data: ["储能(实际)", "储能(指令)", "电网(实际)", "电网(计划)", "光伏(实际)", "光伏(预测)", "CHP(实际)", "CHP(指令)"],
      textStyle: { fontSize: 10 },
    },
    grid: { top: 40, left: 50, right: 20, bottom: 50 },
    xAxis: {
      type: "category", data: labels, axisLabel: { fontSize: 10 },
      axisLine: {
        lineStyle: { color: "#d1d1d6" },
        onZero: false,
      },
    },
    yAxis: { type: "value", name: "kW", axisLabel: { fontSize: 10 } },
    series: [
      // 储能 — 实际(实线蓝) + 指令(虚线蓝)
      { name: "储能(实际)", type: "line", data: batActualSeries, smooth: true,
        lineStyle: { color: "#0071e3", width: 2 }, symbol: "none",
        connectNulls: false },
      { name: "储能(指令)", type: "line", data: batPlan, smooth: false, step: "end",
        lineStyle: { color: "#0071e3", type: "dashed", width: 1.5 }, symbol: "diamond", symbolSize: 4,
        connectNulls: false },
      // 电网 — 实际(实线红) + 计划(虚线红)
      { name: "电网(实际)", type: "line", data: gridActualSeries, smooth: true,
        lineStyle: { color: "#ff3b30", width: 2 }, symbol: "none" },
      { name: "电网(计划)", type: "line", data: gridPlan, smooth: false, step: "end",
        lineStyle: { color: "#ff3b30", type: "dashed", width: 1.5 }, symbol: "diamond", symbolSize: 4 },
      // 光伏 — 实际(实线绿) + 预测(虚线绿)
      { name: "光伏(实际)", type: "line", data: pvActualSeries, smooth: true,
        lineStyle: { color: "#34c759", width: 2 }, symbol: "none",
        areaStyle: { color: "rgba(52,199,89,0.1)", opacity: 0.5 } },
      { name: "光伏(预测)", type: "line", data: pvPlan, smooth: false, step: "end",
        lineStyle: { color: "#34c759", type: "dashed", width: 1.5 }, symbol: "diamond", symbolSize: 4 },
      // CHP — 实际(实线橙) + 指令(虚线橙)
      { name: "CHP(实际)", type: "line", data: chpActualSeries, smooth: true,
        lineStyle: { color: "#ff9500", width: 2 }, symbol: "none" },
      { name: "CHP(指令)", type: "line", data: chpPlan, smooth: false, step: "end",
        lineStyle: { color: "#ff9500", type: "dashed", width: 1.5 }, symbol: "diamond", symbolSize: 4 },
    ],
    // 分隔线标注 "现在"
    markLine: PAST_STEPS > 0 ? {
      silent: true,
      symbol: "none",
      lineStyle: { color: "#8e8e93", type: "solid", width: 1 },
      label: { formatter: "现在", position: "start", fontSize: 10, color: "#8e8e93" },
      data: [{ xAxis: nowMark }],
    } : undefined,
  };
});

// ═══════════════════════════════════════════════════════════
// SOC 轨迹图 (24h = 288 steps, 每小时采样)
// ═══════════════════════════════════════════════════════════

const socChartOption = computed(() => {
  const steps = schedule.value;
  if (steps.length === 0) return {};

  // 每小时取一个点
  const hourly: ScheduleStep[] = [];
  for (let i = 0; i < steps.length; i += 12) hourly.push(steps[i]);

  const labels = hourly.map(s => `${(s.minute / 60).toFixed(0)}h`);
  const batSoc = hourly.map(s => +(s.SOC_bat * 100).toFixed(1));
  const tsSoc = hourly.map(s => +(s.SOC_ts * 100).toFixed(1));

  return {
    title: { text: "储能 SOC 轨迹 (24h)", left: "center", textStyle: { fontSize: 13, color: "#1d1d1f" } },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => `${v.toFixed(1)}%` },
    legend: { bottom: 0, data: ["电池 SOC", "蓄能罐 SOC"], textStyle: { fontSize: 11 } },
    grid: { top: 40, left: 50, right: 20, bottom: 40 },
    xAxis: { type: "category", data: labels, axisLabel: { fontSize: 10 } },
    yAxis: { type: "value", name: "%", min: 0, max: 100, axisLabel: { fontSize: 10 } },
    series: [
      { name: "电池 SOC", type: "line", data: batSoc, smooth: true,
        lineStyle: { color: "#0071e3", width: 2 }, areaStyle: { color: "rgba(0,113,227,0.1)" }, symbol: "none" },
      { name: "蓄能罐 SOC", type: "line", data: tsSoc, smooth: true,
        lineStyle: { color: "#ff6b35", width: 2 }, areaStyle: { color: "rgba(255,107,53,0.1)" }, symbol: "none" },
    ],
  };
});

// ═══════════════════════════════════════════════════════════
// 手动控制 (保留原有逻辑)
// ═══════════════════════════════════════════════════════════

interface ModeDef { value: number; label: string; desc: string }
interface DeviceControlConfig {
  modes: ModeDef[];
  powerLabel: (m: number) => string;
  powerHint: (m: number) => string;
  showPower: (m: number) => boolean;
  powerDefault: number;
}
const deviceControls: Record<string, DeviceControlConfig> = {
  pv_inverter: {
    modes: [
      { value: 0, label: "自动运行 (MILP)", desc: "恢复 MILP 优化控制" },
      { value: 1, label: "限功率", desc: "限制光伏出力不超过设定值" },
    ],
    showPower: (m) => m === 1,
    powerLabel: () => "功率上限", powerHint: () => "限制光伏出力上限 (W)", powerDefault: 50000,
  },
  battery_pcs: {
    modes: [
      { value: 0, label: "自动运行 (MILP)", desc: "恢复 MILP 优化控制" },
      { value: 1, label: "强制充电", desc: "以设定功率充电" },
      { value: 2, label: "强制放电", desc: "以设定功率放电" },
      { value: 3, label: "停机待机", desc: "停止充放电" },
    ],
    showPower: (m) => m === 1 || m === 2,
    powerLabel: (m) => m === 1 ? "充电功率" : "放电功率",
    powerHint: (m) => m === 1 ? "充电功率 (W)" : "放电功率 (W)", powerDefault: 25000,
  },
  chp: {
    modes: [
      { value: 0, label: "自动运行 (MILP)", desc: "恢复 MILP 优化控制" },
      { value: 1, label: "以电定热", desc: "设定发电功率，余热跟随" },
      { value: 2, label: "停机", desc: "强制停机关闭" },
    ],
    showPower: (m) => m === 1,
    powerLabel: () => "发电功率", powerHint: () => "CHP 发电功率设定 (W)", powerDefault: 30000,
  },
  heatpump: {
    modes: [
      { value: 0, label: "自动运行 (MILP)", desc: "恢复 MILP 优化控制" },
      { value: 1, label: "强制制热", desc: "以设定功率制热" },
      { value: 2, label: "强制制冷", desc: "以设定功率制冷" },
      { value: 3, label: "停机", desc: "强制停机" },
    ],
    showPower: (m) => m === 1 || m === 2,
    powerLabel: (m) => m === 1 ? "制热电功率" : "制冷电功率",
    powerHint: (m) => m === 1 ? "制热耗电功率 (W)" : "制冷耗电功率 (W)", powerDefault: 18000,
  },
  thermal_storage: {
    modes: [
      { value: 0, label: "自动运行 (MILP)", desc: "恢复 MILP 优化控制" },
      { value: 1, label: "强制蓄热", desc: "以设定功率蓄热" },
      { value: 2, label: "强制放热", desc: "以设定功率放热" },
      { value: 3, label: "强制蓄冷", desc: "以设定功率蓄冷" },
      { value: 4, label: "强制放冷", desc: "以设定功率放冷" },
      { value: 5, label: "停机", desc: "强制停机" },
    ],
    showPower: (m) => m >= 1 && m <= 4,
    powerLabel: (m) => ["", "蓄热功率", "放热功率", "蓄冷功率", "放冷功率"][m],
    powerHint: (m) => ["", "蓄热功率 (W)", "放热功率 (W)", "蓄冷功率 (W)", "放冷功率 (W)"][m],
    powerDefault: 20000,
  },
};
const durationPresets = [
  { value: 0, label: "永久有效" }, { value: 60, label: "1 分钟" },
  { value: 300, label: "5 分钟" }, { value: 900, label: "15 分钟" },
  { value: 1800, label: "30 分钟" }, { value: 3600, label: "1 小时" },
];

const selectedInfo = () => devices.value.find(d => d.device_id === selectedDevice.value);
const devType = () => selectedInfo()?.dev_type || "";
const ctrl = computed(() => deviceControls[devType()] || null);
const modeOptions = computed(() => ctrl.value?.modes || []);
const showPower = computed(() => ctrl.value?.showPower(cmdMode.value) || false);
const powerLabel = computed(() => ctrl.value?.powerLabel(cmdMode.value) || "");
const powerHint = computed(() => ctrl.value?.powerHint(cmdMode.value) || "");

const modeLabels: Record<number, string> = {};
for (const [_, cfg] of Object.entries(deviceControls)) {
  for (const m of cfg.modes) modeLabels[m.value] = m.label;
}

async function fetchDevices() {
  try {
    const res = await fetch(`${API_BASE}/api/devices`);
    devices.value = await res.json();
    if (devices.value.length > 0 && !selectedDevice.value) {
      selectedDevice.value = devices.value[0].device_id;
      onDeviceChange();
    }
  } catch { /* */ }
}
async function fetchHistory() {
  if (!selectedDevice.value) return;
  try {
    const res = await fetch(`${API_BASE}/api/devices/${selectedDevice.value}/commands?limit=20`);
    const json = await res.json();
    cmdHistory.value = json.commands || [];
  } catch { /* */ }
}
function onDeviceChange() {
  cmdMode.value = 0; powerSetpoint.value = null; fetchHistory();
}
async function sendCommand() {
  sending.value = true; lastResult.value = null;
  const values = powerSetpoint.value != null ? [powerSetpoint.value] : [];
  try {
    const res = await fetch(`${API_BASE}/api/devices/${selectedDevice.value}/command`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ addr: 50, values, mode: cmdMode.value, duration: duration.value }),
    });
    lastResult.value = await res.json();
    setTimeout(() => { fetchHistory(); fetchMilpData(); }, 500);
  } catch (e: any) {
    lastResult.value = { success: false, message: `网络错误: ${e.message || ""}` };
  } finally { sending.value = false; }
}
function formatTs(ts: string): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleTimeString("zh-CN", { hour12: false });
}

const overrideDevices = computed(() => {
  const list: { name: string; remaining: number }[] = [];
  for (const [did, remaining] of Object.entries(overrides.value)) {
    const dev = devices.value.find(d => d.device_id === did);
    list.push({ name: dev?.name || did, remaining: Math.round(remaining) });
  }
  return list;
});

// ═══════════════════════════════════════════════════════════
// 生命周期
// ═══════════════════════════════════════════════════════════

onMounted(() => {
  fetchMilpData();
  fetchDevices();
  refreshTimer = window.setInterval(fetchMilpData, 15000);
});
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer);
});
watch(activeTab, (tab) => {
  if (tab === "milp") fetchMilpData();
});
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1>控制面板</h1>
      <p>MILP 自动优化调度 + 手动控制指令（手动指令优先级高于自动优化）</p>
    </div>

    <!-- ── 模式切换 ── -->
    <el-tabs v-model="activeTab" class="mode-tabs">
      <el-tab-pane label="MILP 自动优化" name="milp" />
      <el-tab-pane name="manual">
        <template #label>
          <span>手动控制 <el-tag size="small" type="danger" style="margin-left:6px">高优先级</el-tag></span>
        </template>
      </el-tab-pane>
    </el-tabs>

    <!-- ══════════════════════════════════════════════════════ -->
    <!-- MILP 自动优化 Tab -->
    <!-- ══════════════════════════════════════════════════════ -->
    <div v-if="activeTab === 'milp'">
      <!-- 状态卡片 -->
      <el-row :gutter="16" style="margin-bottom:16px">
        <el-col :span="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-label">求解状态</div>
            <div class="stat-value" :class="milpStatus?.status === 'Optimal' ? 'text-green' : 'text-red'">
              {{ milpStatus?.status || "—" }}
            </div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-label">预计 24h 总成本</div>
            <div class="stat-value">¥{{ milpStatus?.total_cost_yuan?.toFixed(0) || "—" }}</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-label">优化步长 / 时域</div>
            <div class="stat-value">{{ milpStatus?.step_minutes || "—" }}min / {{ milpStatus?.num_steps || "—" }}步</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-label">最近求解</div>
            <div class="stat-value stat-time">{{ milpStatus?.last_solve_ts ? new Date(milpStatus.last_solve_ts).toLocaleTimeString("zh-CN", { hour12: false }) : "—" }}</div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 当前步调度值 -->
      <el-card shadow="hover" style="margin-bottom:16px" v-if="schedule.length > 0">
        <template #header>
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span>当前指令 — 计划 {{ schedule[0].minute }} 分钟内执行完成，下一个 5min 重新优化</span>
            <el-button size="small" @click="fetchMilpData" text>刷新</el-button>
          </div>
        </template>
        <el-row :gutter="12">
          <el-col :span="4"><div class="dispatch-item pv"><span class="d-val">{{ schedule[0].P_pv_kw.toFixed(0) }}</span><span class="d-unit">kW</span><span class="d-label">光伏</span></div></el-col>
          <el-col :span="4"><div class="dispatch-item bat"><span class="d-val">{{ (schedule[0].P_bat_dis_kw - schedule[0].P_bat_ch_kw).toFixed(0) }}</span><span class="d-unit">kW</span><span class="d-label">储能净输出</span></div></el-col>
          <el-col :span="4"><div class="dispatch-item chp"><span class="d-val">{{ schedule[0].P_chp_kw.toFixed(0) }}</span><span class="d-unit">kW</span><span class="d-label">{{ schedule[0].u_chp ? "CHP 运行中" : "CHP 停机" }}</span></div></el-col>
          <el-col :span="4"><div class="dispatch-item grid"><span class="d-val">{{ (schedule[0].P_grid_import_kw - schedule[0].P_grid_export_kw).toFixed(0) }}</span><span class="d-unit">kW</span><span class="d-label">电网净购电</span></div></el-col>
          <el-col :span="4"><div class="dispatch-item hp"><span class="d-val">{{ schedule[0].P_hp_kw.toFixed(0) }}</span><span class="d-unit">kW</span><span class="d-label">热泵</span></div></el-col>
          <el-col :span="4"><div class="dispatch-item soc"><span class="d-val">{{ (schedule[0].SOC_bat * 100).toFixed(0) }}%</span><span class="d-unit"> / {{ (schedule[0].SOC_ts * 100).toFixed(0) }}%</span><span class="d-label">电池 / 蓄能 SOC</span></div></el-col>
        </el-row>
      </el-card>

      <!-- 功率调度图 -->
      <el-card shadow="hover" style="margin-bottom:16px" v-if="schedule.length >= 2">
        <VChart :option="powerChartOption" style="height:280px" autoresize />
      </el-card>

      <!-- SOC 轨迹图 -->
      <el-card shadow="hover" style="margin-bottom:16px" v-if="schedule.length >= 12">
        <VChart :option="socChartOption" style="height:280px" autoresize />
      </el-card>

      <!-- 手动覆盖状态 -->
      <el-card shadow="hover">
        <template #header><span>手动覆盖状态</span></template>
        <div v-if="overrideDevices.length === 0" style="color:var(--el-text-color-secondary);font-size:14px">
          <el-icon style="color:#34c759"><svg viewBox="0 0 1024 1024" width="1em" height="1em"><path d="M512 64C264.6 64 64 264.6 64 512s200.6 448 448 448 448-200.6 448-448S759.4 64 512 64zm193.5 301.7l-210.6 292a31.8 31.8 0 01-51.7 0L318.5 484.9c-3.8-5.3 0-12.7 6.5-12.7h46.9c10.2 0 19.9 4.9 25.9 13.3l71.2 98.8 157.2-218c6-8.3 15.6-13.3 25.9-13.3H699c6.5 0 10.3 7.4 6.5 12.7z" fill="currentColor"/></svg></el-icon>
          全部设备由 MILP 自动优化控制
        </div>
        <div v-else>
          <el-tag v-for="ov in overrideDevices" :key="ov.name" type="warning" style="margin-right:8px;margin-bottom:4px">
            {{ ov.name }} — 手动控制中 (剩余 {{ ov.remaining }}s)
          </el-tag>
          <div style="margin-top:8px;font-size:12px;color:var(--el-text-color-secondary)">
            手动控制期间，MILP 不会向该设备下发指令。切换到自动模式或等待过期后恢复。
          </div>
        </div>
      </el-card>
    </div>

    <!-- ══════════════════════════════════════════════════════ -->
    <!-- 手动控制 Tab (保留原有设计) -->
    <!-- ══════════════════════════════════════════════════════ -->
    <div v-if="activeTab === 'manual'">
      <el-alert type="warning" :closable="false" show-icon style="margin-bottom:16px"
        title="手动控制优先级高于 MILP 自动优化。下发手动指令后，MILP 将停止向该设备下发调度指令，直到手动模式过期或切回自动。">
      </el-alert>

      <el-row :gutter="20">
        <el-col :span="10">
          <el-card>
            <template #header><span>下发控制指令</span></template>
            <el-alert v-if="!ctrl" type="info" show-icon :closable="false"
              title="该设备为传感器/仪表，不支持远程控制指令。" style="margin-bottom: 16px" />
            <el-form label-width="80px" v-if="ctrl">
              <el-form-item label="目标设备">
                <el-select v-model="selectedDevice" style="width: 100%" @change="onDeviceChange">
                  <el-option v-for="d in devices" :key="d.device_id" :label="d.name" :value="d.device_id" />
                </el-select>
              </el-form-item>
              <el-form-item label="控制模式">
                <el-radio-group v-model="cmdMode">
                  <el-radio v-for="m in modeOptions" :key="m.value" :value="m.value">{{ m.label }}</el-radio>
                </el-radio-group>
              </el-form-item>
              <el-form-item v-if="showPower" :label="powerLabel">
                <el-input-number v-model="powerSetpoint" :min="0" :max="selectedInfo()?.rated_power_w || 100000" :step="1000" :placeholder="ctrl.powerDefault.toString()" />
                <span style="margin-left: 8px; color: var(--el-text-color-secondary); font-size: 12px">{{ powerHint }}</span>
              </el-form-item>
              <el-form-item label="生效时长">
                <el-select v-model="duration" style="width: 200px" :disabled="cmdMode === 0">
                  <el-option v-for="p in durationPresets" :key="p.value" :label="p.label" :value="p.value" />
                </el-select>
                <span v-if="cmdMode === 0" style="margin-left: 8px; color: var(--el-text-color-secondary); font-size: 12px">自动模式下无需设置时效</span>
              </el-form-item>
              <el-form-item>
                <el-button type="primary" @click="sendCommand" :loading="sending">下发指令</el-button>
              </el-form-item>
            </el-form>
            <el-alert v-if="lastResult" :title="lastResult.message" :type="lastResult.success ? 'success' : 'error'" closable show-icon style="margin-bottom:8px" />
          </el-card>
        </el-col>
        <el-col :span="14">
          <el-card style="margin-bottom: 16px">
            <template #header><span>{{ selectedInfo()?.name || "设备" }} 控制模式说明</span></template>
            <div style="line-height:2;color:var(--el-text-color-secondary);font-size:14px">
              <div v-for="m in modeOptions" :key="m.value" style="display:flex;gap:8px">
                <span style="font-weight:600;min-width:90px;color:var(--el-text-color)">{{ m.label }}</span>
                <span>— {{ m.desc }}</span>
              </div>
            </div>
          </el-card>
          <el-card>
            <template #header>
              <div style="display:flex;justify-content:space-between;align-items:center">
                <span>指令历史</span>
                <el-button size="small" @click="fetchHistory">刷新</el-button>
              </div>
            </template>
            <el-table :data="cmdHistory" size="small" max-height="300" empty-text="暂无指令记录">
              <el-table-column label="时间" width="90"><template #default="{ row }">{{ formatTs(row.ts) }}</template></el-table-column>
              <el-table-column label="模式" width="110">
                <template #default="{ row }"><el-tag size="small" :type="row.cmd_mode === 0 ? 'info' : 'warning'">{{ modeLabels[row.cmd_mode] || row.cmd_mode }}</el-tag></template>
              </el-table-column>
              <el-table-column prop="cmd_values" label="功率(W)" width="90" />
              <el-table-column label="时效" width="80"><template #default="{ row }">{{ row.cmd_duration > 0 ? row.cmd_duration + 's' : '永久' }}</template></el-table-column>
              <el-table-column label="结果" width="70"><template #default="{ row }"><el-tag :type="row.success ? 'success' : 'danger'" size="small">{{ row.success ? "已下发" : "失败" }}</el-tag></template></el-table-column>
              <el-table-column prop="message" label="详情" min-width="120" show-overflow-tooltip />
            </el-table>
          </el-card>
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<style scoped>
.mode-tabs { margin-bottom: 16px; }
.mode-tabs :deep(.el-tabs__header) { margin-bottom: 0; }
.stat-card { text-align: center; }
.stat-label { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
.stat-value { font-size: 20px; font-weight: 600; color: var(--el-text-color); }
.stat-value.text-green { color: #34c759; }
.stat-value.text-red { color: #ff3b30; }
.stat-value.stat-time { font-size: 14px; }
.dispatch-item { text-align: center; padding: 8px 4px; border-radius: 6px; background: var(--el-fill-color-lighter); }
.dispatch-item .d-val { display: block; font-size: 20px; font-weight: 600; }
.dispatch-item .d-unit { font-size: 11px; color: var(--el-text-color-secondary); margin-left: 2px; }
.dispatch-item .d-label { display: block; font-size: 11px; color: var(--el-text-color-secondary); margin-top: 2px; }
.dispatch-item.pv .d-val { color: #34c759; }
.dispatch-item.bat .d-val { color: #0071e3; }
.dispatch-item.chp .d-val { color: #ff9500; }
.dispatch-item.grid .d-val { color: #ff3b30; }
.dispatch-item.hp .d-val { color: #af52de; }
.dispatch-item.soc .d-val { color: var(--el-text-color); }
</style>
