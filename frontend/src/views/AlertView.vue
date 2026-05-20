<script setup lang="ts">
import { ref, onMounted, computed } from "vue";

const API_BASE = "http://localhost:8000";

interface Alert {
  ts: string;
  device_id: string;
  alert_name: string;
  severity: string;
  val: number;
  threshold: number;
  status: string;
  message: string;
}

const alerts = ref<Alert[]>([]);
const stats = ref({ active: 0, acked: 0, resolved: 0 });
const loading = ref(false);
const filterStatus = ref("");
const filterSeverity = ref("");

const deviceLabels: Record<string, string> = {
  pv_inverter_01: "光伏逆变器 #1",
  battery_pcs_01: "储能变流器 #1",
  system: "系统",
};

async function fetchAlerts() {
  loading.value = true;
  const params = new URLSearchParams();
  if (filterStatus.value) params.set("status", filterStatus.value);
  if (filterSeverity.value) params.set("severity", filterSeverity.value);
  params.set("limit", "100");
  try {
    const [a, s] = await Promise.all([
      fetch(`${API_BASE}/api/alerts?${params}`).then(r => r.json()),
      fetch(`${API_BASE}/api/alerts/stats`).then(r => r.json()),
    ]);
    alerts.value = a.alerts || [];
    stats.value = s;
  } catch (e) { console.error(e); }
  finally { loading.value = false; }
}

async function ackAlert(alert: Alert) {
  await fetch(`${API_BASE}/api/alerts/${alert.ts}/ack?device_id=${alert.device_id}`, { method: "POST" });
  fetchAlerts();
}

onMounted(fetchAlerts);

function severityTag(sev: string) {
  return sev === "critical" ? "danger" : sev === "warning" ? "warning" : "info";
}
function statusTag(st: string) {
  return st === "active" ? "danger" : st === "acked" ? "warning" : "info";
}
function statusLabel(st: string) {
  return st === "active" ? "活跃" : st === "acked" ? "已确认" : "已恢复";
}
function formatTs(ts: string) {
  return ts ? new Date(ts).toLocaleString("zh-CN", { hour12: false }) : "-";
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1>告警管理</h1>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="16" style="margin-bottom: 20px">
      <el-col :span="8">
        <el-statistic title="活跃告警" :value="stats.active">
          <template #suffix>
            <el-tag type="danger" size="small">{{ stats.active }}</el-tag>
          </template>
        </el-statistic>
      </el-col>
      <el-col :span="8">
        <el-statistic title="已确认" :value="stats.acked">
          <template #suffix>
            <el-tag type="warning" size="small">{{ stats.acked }}</el-tag>
          </template>
        </el-statistic>
      </el-col>
      <el-col :span="8">
        <el-statistic title="已恢复" :value="stats.resolved">
          <template #suffix>
            <el-tag type="info" size="small">{{ stats.resolved }}</el-tag>
          </template>
        </el-statistic>
      </el-col>
    </el-row>

    <!-- 筛选栏 -->
    <el-card shadow="hover" style="margin-bottom: 16px">
      <div style="display:flex; gap: 12px; align-items: center">
        <el-select v-model="filterSeverity" placeholder="告警级别" clearable style="width:140px" @change="fetchAlerts">
          <el-option label="严重" value="critical" />
          <el-option label="重要" value="warning" />
        </el-select>
        <el-select v-model="filterStatus" placeholder="状态" clearable style="width:120px" @change="fetchAlerts">
          <el-option label="活跃" value="active" />
          <el-option label="已确认" value="acked" />
          <el-option label="已恢复" value="resolved" />
        </el-select>
        <el-button @click="fetchAlerts" :loading="loading">刷新</el-button>
      </div>
    </el-card>

    <!-- 告警列表 -->
    <el-card shadow="hover" v-loading="loading">
      <el-table :data="alerts" size="small" max-height="500" empty-text="暂无告警">
        <el-table-column label="时间" width="155">
          <template #default="{ row }">{{ formatTs(row.ts) }}</template>
        </el-table-column>
        <el-table-column label="级别" width="70">
          <template #default="{ row }">
            <el-tag :type="severityTag(row.severity)" size="small">
              {{ row.severity === "critical" ? "严重" : "重要" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="alert_name" label="告警名称" width="140" />
        <el-table-column label="设备" width="130">
          <template #default="{ row }">{{ deviceLabels[row.device_id] || row.device_id }}</template>
        </el-table-column>
        <el-table-column label="当前值" width="80">
          <template #default="{ row }">{{ row.val.toFixed(1) }}</template>
        </el-table-column>
        <el-table-column label="阈值" width="70">
          <template #default="{ row }">{{ row.threshold }}</template>
        </el-table-column>
        <el-table-column prop="message" label="详情" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'active'"
              size="small"
              @click="ackAlert(row)"
            >确认</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
</style>
