<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import { API_BASE } from "../config";

interface MilpStatus { status: string; total_cost_yuan: number; last_solve_ts: string; running: boolean; }
interface AlertRow { alert_name: string; severity: string; status: string; ts: string; device_id: string; message: string; }
interface RuleExec { rule_name: string; executed: boolean; ts: string; }
interface CmdRow { success: boolean; ts: string; device_id: string; cmd_mode: number; }

const milp = ref<MilpStatus | null>(null);
const alerts = ref<AlertRow[]>([]);
const alertStats = ref({ active: 0, acked: 0, resolved: 0 });
const ruleExecs = ref<RuleExec[]>([]);
const cmdLog = ref<CmdRow[]>([]);
const cmdStats = ref({ total: 0, success: 0 });
const loading = ref(true);

async function fetchAll() {
  loading.value = true;
  try {
    const [m, a, as, r, c] = await Promise.all([
      fetch(`${API_BASE}/api/milp/status`).then(r => r.json()),
      fetch(`${API_BASE}/api/alerts?limit=20`).then(r => r.json()),
      fetch(`${API_BASE}/api/alerts/stats`).then(r => r.json()),
      fetch(`${API_BASE}/api/rules/executions?limit=20`).then(r => r.json()),
      fetch(`${API_BASE}/api/devices/battery_pcs_01/commands?limit=50`).then(r => r.json()),
    ]);
    milp.value = m;
    alerts.value = (a.alerts || []).slice(0, 12);
    alertStats.value = as;
    ruleExecs.value = (r.executions || []).slice(0, 12);
    const cmds = (c.commands || []) as CmdRow[];
    cmdLog.value = cmds.slice(0, 12);
    cmdStats.value = {
      total: cmds.length,
      success: cmds.filter(c => c.success).length,
    };
  } catch { /* */ }
  finally { loading.value = false; }
}

onMounted(fetchAll);

const successPct = computed(() =>
  cmdStats.value.total > 0
    ? (cmdStats.value.success / cmdStats.value.total * 100).toFixed(0)
    : "—"
);

const severityColor = (s: string) => s === "critical" ? "danger" : s === "warning" ? "warning" : "info";
const statusColor = (s: string) => s === "active" ? "danger" : s === "resolved" ? "success" : "info";
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1>系统监控</h1>
      <el-button size="small" @click="fetchAll" :loading="loading">刷新</el-button>
    </div>

    <!-- KPI 行 -->
    <el-row :gutter="16" style="margin-bottom:20px">
      <el-col :span="6">
        <el-card shadow="hover"><div class="stat-card">
          <div class="stat-num">{{ milp?.status || "—" }}</div>
          <div class="stat-lbl">MILP 求解状态</div>
          <div v-if="milp?.running" style="color:#34c759;font-size:12px">引擎运行中</div>
        </div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover"><div class="stat-card">
          <div class="stat-num">¥{{ milp?.total_cost_yuan?.toFixed(0) || "—" }}</div>
          <div class="stat-lbl">24h 预计成本</div>
        </div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover"><div class="stat-card">
          <div class="stat-num">{{ alertStats.active }} / {{ alertStats.resolved }}</div>
          <div class="stat-lbl">活跃告警 / 已恢复</div>
        </div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover"><div class="stat-card">
          <div class="stat-num">{{ successPct }}%</div>
          <div class="stat-lbl">指令成功率 ({{ cmdStats.success }}/{{ cmdStats.total }})</div>
        </div></el-card>
      </el-col>
    </el-row>

    <!-- 详细日志 -->
    <el-row :gutter="20">
      <!-- 左侧：告警 + 规则 -->
      <el-col :span="12">
        <el-card shadow="hover" style="margin-bottom:20px">
          <template #header><span>告警日志 (最近 12 条)</span></template>
          <el-table :data="alerts" size="small" max-height="350" empty-text="无告警">
            <el-table-column label="时间" width="90">
              <template #default="{ row }">{{ row.ts?.slice(11, 19) }}</template>
            </el-table-column>
            <el-table-column prop="alert_name" label="告警" min-width="100" show-overflow-tooltip />
            <el-table-column label="级别" width="60">
              <template #default="{ row }"><el-tag :type="severityColor(row.severity)" size="small">{{ row.severity }}</el-tag></template>
            </el-table-column>
            <el-table-column label="状态" width="60">
              <template #default="{ row }"><el-tag :type="statusColor(row.status)" size="small">{{ row.status }}</el-tag></template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <!-- 右侧：规则执行 + 指令 -->
      <el-col :span="12">
        <el-card shadow="hover" style="margin-bottom:20px">
          <template #header><span>规则执行日志 (最近 12 条)</span></template>
          <el-table :data="ruleExecs" size="small" max-height="170" empty-text="无记录">
            <el-table-column label="时间" width="90">
              <template #default="{ row }">{{ row.ts?.slice(11, 19) }}</template>
            </el-table-column>
            <el-table-column prop="rule_name" label="规则" min-width="100" show-overflow-tooltip />
            <el-table-column label="结果" width="50">
              <template #default="{ row }">
                <el-tag :type="row.executed ? 'success' : 'danger'" size="small">{{ row.executed ? "√" : "×" }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="hover">
          <template #header><span>指令下发日志 (最近 12 条)</span></template>
          <el-table :data="cmdLog" size="small" max-height="170" empty-text="无记录">
            <el-table-column label="时间" width="90">
              <template #default="{ row }">{{ row.ts?.slice(11, 19) }}</template>
            </el-table-column>
            <el-table-column prop="device_id" label="设备" width="110" />
            <el-table-column label="模式" width="50">
              <template #default="{ row }">{{ row.cmd_mode }}</template>
            </el-table-column>
            <el-table-column label="结果" width="50">
              <template #default="{ row }">
                <el-tag :type="row.success ? 'success' : 'danger'" size="small">{{ row.success ? "√" : "×" }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.page-header { display: flex; justify-content: space-between; align-items: center; }
.stat-card { text-align: center; padding: 12px 0; }
.stat-num { font-size: 22px; font-weight: 700; color: var(--el-text-color); }
.stat-lbl { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
</style>
