<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import { API_BASE } from "../config";

interface Device {
  device_id: string; name: string; dev_type: string; rated_power_w: number;
}

interface CmdRecord {
  ts: string; cmd_register: number; cmd_values: string;
  cmd_mode: number; cmd_duration: number; success: boolean; message: string;
}

const devices = ref<Device[]>([]);
const selectedDevice = ref("");
const cmdMode = ref(0);
const powerSetpoint = ref<number | null>(null);
const duration = ref(0);
const sending = ref(false);
const lastResult = ref<{ success: boolean; message: string } | null>(null);
const cmdHistory = ref<CmdRecord[]>([]);

// ── 设备类型 → 控制模式定义 ─────────────────────────────

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
      { value: 0, label: "自动运行", desc: "MPPT 最大功率跟踪" },
      { value: 1, label: "限功率", desc: "限制光伏出力不超过设定值" },
    ],
    showPower: (m) => m === 1,
    powerLabel: () => "功率上限",
    powerHint: () => "限制光伏出力上限 (W)",
    powerDefault: 50000,
  },
  battery_pcs: {
    modes: [
      { value: 0, label: "自动运行", desc: "分时电价自动充放电" },
      { value: 1, label: "强制充电", desc: "以设定功率充电" },
      { value: 2, label: "强制放电", desc: "以设定功率放电" },
      { value: 3, label: "停机待机", desc: "停止充放电" },
    ],
    showPower: (m) => m === 1 || m === 2,
    powerLabel: (m) => m === 1 ? "充电功率" : "放电功率",
    powerHint: (m) => m === 1 ? "充电功率 (W)" : "放电功率 (W)",
    powerDefault: 25000,
  },
  chp: {
    modes: [
      { value: 0, label: "自动运行", desc: "按季节/时段自动启停" },
      { value: 1, label: "以电定热", desc: "设定发电功率，余热跟随" },
      { value: 2, label: "停机", desc: "强制停机关闭" },
    ],
    showPower: (m) => m === 1,
    powerLabel: () => "发电功率",
    powerHint: () => "CHP 发电功率设定 (W)",
    powerDefault: 30000,
  },
  heatpump: {
    modes: [
      { value: 0, label: "自动运行", desc: "按季节自动制热/制冷" },
      { value: 1, label: "强制制热", desc: "以设定功率制热" },
      { value: 2, label: "强制制冷", desc: "以设定功率制冷" },
      { value: 3, label: "停机", desc: "强制停机" },
    ],
    showPower: (m) => m === 1 || m === 2,
    powerLabel: (m) => m === 1 ? "制热电功率" : "制冷电功率",
    powerHint: (m) => m === 1 ? "制热耗电功率 (W)" : "制冷耗电功率 (W)",
    powerDefault: 18000,
  },
  thermal_storage: {
    modes: [
      { value: 0, label: "自动运行", desc: "按季节/时段自动蓄放" },
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
  { value: 0, label: "永久有效" },
  { value: 60, label: "1 分钟" },
  { value: 300, label: "5 分钟" },
  { value: 900, label: "15 分钟" },
  { value: 1800, label: "30 分钟" },
  { value: 3600, label: "1 小时" },
];

// ── 计算属性 ─────────────────────────────────────────────

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

// ── API ──────────────────────────────────────────────────

async function fetchDevices() {
  try {
    const res = await fetch(`${API_BASE}/api/devices`);
    devices.value = await res.json();
    if (devices.value.length > 0 && !selectedDevice.value) {
      selectedDevice.value = devices.value[0].device_id;
      onDeviceChange();
    }
  } catch (e) { console.error(e); }
}

async function fetchHistory() {
  if (!selectedDevice.value) return;
  try {
    const res = await fetch(`${API_BASE}/api/devices/${selectedDevice.value}/commands?limit=20`);
    const json = await res.json();
    cmdHistory.value = json.commands || [];
  } catch (e) { console.error(e); }
}

function onDeviceChange() {
  cmdMode.value = 0;
  powerSetpoint.value = null;
  fetchHistory();
}

onMounted(() => { fetchDevices(); });

async function sendCommand() {
  sending.value = true;
  lastResult.value = null;
  const values = powerSetpoint.value != null ? [powerSetpoint.value] : [];
  try {
    const res = await fetch(`${API_BASE}/api/devices/${selectedDevice.value}/command`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ addr: 50, values, mode: cmdMode.value, duration: duration.value }),
    });
    lastResult.value = await res.json();
    setTimeout(() => fetchHistory(), 500);
  } catch (e: any) {
    lastResult.value = { success: false, message: `网络错误: ${e.message || "请检查后端是否运行"}` };
  } finally { sending.value = false; }
}

function formatTs(ts: string): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleTimeString("zh-CN", { hour12: false });
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1>控制指令</h1>
      <p>根据不同设备类型下发专属控制指令</p>
    </div>

    <el-row :gutter="20">
      <!-- 左侧：指令下发 -->
      <el-col :span="10">
        <el-card>
          <template #header><span>下发控制指令</span></template>

          <!-- 传感器设备提示 -->
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
                <el-radio v-for="m in modeOptions" :key="m.value" :value="m.value">
                  {{ m.label }}
                </el-radio>
              </el-radio-group>
            </el-form-item>

            <el-form-item v-if="showPower" :label="powerLabel">
              <el-input-number
                v-model="powerSetpoint"
                :min="0"
                :max="selectedInfo()?.rated_power_w || 100000"
                :step="1000"
                :placeholder="ctrl.powerDefault.toString()"
              />
              <span style="margin-left: 8px; color: var(--apple-text-tertiary); font-size: 12px">
                {{ powerHint }}
              </span>
            </el-form-item>

            <el-form-item label="生效时长">
              <el-select v-model="duration" style="width: 200px" :disabled="cmdMode === 0">
                <el-option v-for="p in durationPresets" :key="p.value" :label="p.label" :value="p.value" />
              </el-select>
              <span v-if="cmdMode === 0" style="margin-left: 8px; color: var(--apple-text-tertiary); font-size: 12px">
                自动模式下无需设置时效
              </span>
            </el-form-item>

            <el-form-item>
              <el-button type="primary" @click="sendCommand" :loading="sending">下发指令</el-button>
            </el-form-item>
          </el-form>

          <el-alert v-if="lastResult" :title="lastResult.message"
            :type="lastResult.success ? 'success' : 'error'" closable show-icon style="margin-bottom: 8px" />
          <el-alert v-if="lastResult?.success" type="info"
            title="指令已写入 Modbus 寄存器，监控数据将在 5-10 秒内刷新。" :closable="false" style="font-size: 12px" />
        </el-card>
      </el-col>

      <!-- 右侧：设备说明 + 历史 -->
      <el-col :span="14">
        <!-- 当前设备控制说明 -->
        <el-card style="margin-bottom: 16px">
          <template #header><span>{{ selectedInfo()?.name || "设备" }} 控制模式说明</span></template>
          <div style="line-height: 2; color: var(--apple-text-secondary); font-size: 14px">
            <div v-for="m in modeOptions" :key="m.value" style="display:flex;gap:8px">
              <span style="font-weight: 600; min-width: 70px; color: var(--apple-text)">{{ m.label }}</span>
              <span>— {{ m.desc }}</span>
            </div>
          </div>
        </el-card>

        <!-- 指令历史 -->
        <el-card>
          <template #header>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span>指令历史</span>
              <el-button size="small" @click="fetchHistory">刷新</el-button>
            </div>
          </template>
          <el-table :data="cmdHistory" size="small" max-height="300" empty-text="暂无指令记录">
            <el-table-column label="时间" width="90">
              <template #default="{ row }">{{ formatTs(row.ts) }}</template>
            </el-table-column>
            <el-table-column label="模式" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.cmd_mode === 0 ? 'info' : 'warning'">
                  {{ modeLabels[row.cmd_mode] || row.cmd_mode }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="cmd_values" label="功率(W)" width="90" />
            <el-table-column label="时效" width="80">
              <template #default="{ row }">{{ row.cmd_duration > 0 ? row.cmd_duration + 's' : '永久' }}</template>
            </el-table-column>
            <el-table-column label="结果" width="70">
              <template #default="{ row }">
                <el-tag :type="row.success ? 'success' : 'danger'" size="small">{{ row.success ? "已下发" : "失败" }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="message" label="详情" min-width="120" show-overflow-tooltip />
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>
