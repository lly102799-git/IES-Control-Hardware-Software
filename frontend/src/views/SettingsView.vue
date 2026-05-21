<script setup lang="ts">
import { ref, onMounted } from "vue";
import { API_BASE } from "../config";
const activeTab = ref("rules");
const saveResult = ref("");
const saving = ref(false);

// ── 规则引擎 ─────────────────────────────────────────────

interface Condition { field: string; op: string; value?: number; min?: number; max?: number }
interface Action { device: string; mode: number; power_setpoint: number; duration: number }
interface Rule { name: string; description: string; priority: number; enabled: boolean;
  cooldown_seconds: number; conditions: Condition[]; actions: Action[] }

const rules = ref<Rule[]>([]);
const editRule = ref<Rule | null>(null);
const showRuleDialog = ref(false);

const OP_OPTIONS = ["lt","gt","eq","neq","between","not_between","lte","gte"];
const FIELD_OPTIONS = ["hour","soc","pv_power","battery_power","status","temp_battery"];
const DEVICE_OPTIONS = ["battery_pcs_01","pv_inverter_01","chp_01","heatpump_01","thermal_storage_01"];

async function fetchConfig() {
  const [r, a] = await Promise.all([
    fetch(`${API_BASE}/api/config/rules`).then(r => r.json()),
    fetch(`${API_BASE}/api/config/alerts`).then(r => r.json()),
  ]);
  rules.value = r.items || [];
  alerts.value = a.items || [];
}

function openRuleDialog(rule?: Rule) {
  editRule.value = rule ? JSON.parse(JSON.stringify(rule)) : {
    name: "", description: "", priority: 10, enabled: true,
    cooldown_seconds: 300, conditions: [], actions: [],
  };
  showRuleDialog.value = true;
}

function addCondition() {
  if (!editRule.value) return;
  editRule.value.conditions.push({ field: "soc", op: "lt", value: 50 });
}
function removeCondition(idx: number) { editRule.value?.conditions.splice(idx, 1); }
function addAction() {
  if (!editRule.value) return;
  editRule.value.actions.push({ device: "battery_pcs_01", mode: 0, power_setpoint: 0, duration: 0 });
}
function removeAction(idx: number) { editRule.value?.actions.splice(idx, 1); }

function saveRuleDialog() {
  if (!editRule.value) return;
  const idx = rules.value.findIndex(r => r.name === editRule.value!.name);
  if (idx >= 0) rules.value[idx] = editRule.value;
  else rules.value.push(editRule.value);
  showRuleDialog.value = false;
  saveRules();
}
function deleteRule(idx: number) {
  rules.value.splice(idx, 1);
  saveRules();
}

async function saveRules() {
  saving.value = true;
  try {
    const r = await fetch(`${API_BASE}/api/config/rules`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: rules.value }),
    });
    const d = await r.json();
    saveResult.value = d.ok ? `规则已保存 (${d.count}条)` : d.error;
  } catch (e: any) { saveResult.value = "网络错误"; }
  finally { saving.value = false; }
}

// ── 告警配置 ─────────────────────────────────────────────

interface AlertRule { name: string; severity: string; device_id: string; point: string;
  op: string; threshold: number; cooldown_seconds: number; message: string }
const alerts = ref<AlertRule[]>([]);
const editAlert = ref<AlertRule | null>(null);
const showAlertDialog = ref(false);

function openAlertDialog(alert?: AlertRule) {
  editAlert.value = alert ? JSON.parse(JSON.stringify(alert)) : {
    name: "", severity: "warning", device_id: "battery_pcs_01",
    point: "soc", op: "lt", threshold: 50, cooldown_seconds: 300, message: "",
  };
  showAlertDialog.value = true;
}
function saveAlertDialog() {
  if (!editAlert.value) return;
  const idx = alerts.value.findIndex(a => a.name === editAlert.value!.name);
  if (idx >= 0) alerts.value[idx] = editAlert.value;
  else alerts.value.push(editAlert.value);
  showAlertDialog.value = false;
  saveAlerts();
}
function deleteAlert(idx: number) {
  alerts.value.splice(idx, 1);
  saveAlerts();
}

async function saveAlerts() {
  saving.value = true;
  try {
    const r = await fetch(`${API_BASE}/api/config/alerts`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: alerts.value }),
    });
    const d = await r.json();
    saveResult.value = d.ok ? `告警已保存 (${d.count}条)` : d.error;
  } catch { saveResult.value = "网络错误"; }
  finally { saving.value = false; }
}

// ── 设备点表 ─────────────────────────────────────────────

interface PointDef { name: string; register: number; scale: number; is_32bit: boolean; is_signed: boolean }
interface DeviceTable { device_id: string; slave_id: number; read_start: number; read_count: number; points: PointDef[] }
const ptDevices = ref<DeviceTable[]>([]);
const editPtDevice = ref<DeviceTable | null>(null);
const showPtDialog = ref(false);

async function fetchPointTables() {
  try {
    const r = await fetch(`${API_BASE}/api/config/point-tables`).then(r => r.json());
    const devs = r.devices || {};
    ptDevices.value = Object.entries(devs).map(([device_id, cfg]: [string, any]) => ({
      device_id,
      slave_id: cfg.slave_id,
      read_start: cfg.read_start,
      read_count: cfg.read_count,
      points: cfg.points || [],
    }));
  } catch {}
}

function openPtDialog(dev?: DeviceTable) {
  editPtDevice.value = dev ? JSON.parse(JSON.stringify(dev)) : {
    device_id: "", slave_id: 6, read_start: 0, read_count: 10, points: [],
  };
  showPtDialog.value = true;
}

function addPoint() {
  editPtDevice.value?.points.push({ name: "", register: 0, scale: 1.0, is_32bit: false, is_signed: false });
}
function removePoint(idx: number) { editPtDevice.value?.points.splice(idx, 1); }

function savePtDialog() {
  if (!editPtDevice.value) return;
  const idx = ptDevices.value.findIndex(d => d.device_id === editPtDevice.value!.device_id);
  if (idx >= 0) ptDevices.value[idx] = editPtDevice.value;
  else ptDevices.value.push(editPtDevice.value);
  showPtDialog.value = false;
  savePointTables();
}

function deletePtDevice(idx: number) {
  ptDevices.value.splice(idx, 1);
  savePointTables();
}

async function savePointTables() {
  saving.value = true;
  try {
    const items = ptDevices.value.map(d => ({
      device_id: d.device_id, slave_id: d.slave_id,
      read_start: d.read_start, read_count: d.read_count, points: d.points,
    }));
    const r = await fetch(`${API_BASE}/api/config/point-tables`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    const d = await r.json();
    saveResult.value = d.ok ? `点表已保存 (${d.count}台设备)` : d.error;
  } catch { saveResult.value = "网络错误"; }
  finally { saving.value = false; }
}

onMounted(() => { fetchConfig(); fetchPointTables(); });
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1>系统配置</h1>
      <p>在线编辑运行参数，保存后立即生效</p>
    </div>

    <el-tabs v-model="activeTab">
      <!-- ═══ 规则引擎 ═══ -->
      <el-tab-pane label="规则引擎" name="rules">
        <div style="margin-bottom: 12px; display: flex; gap: 8px">
          <el-button size="small" @click="openRuleDialog()">+ 新增规则</el-button>
        </div>

        <el-alert v-if="saveResult && activeTab === 'rules'" :title="saveResult"
          :type="saveResult.includes('失败') ? 'error' : 'success'" show-icon closable style="margin-bottom:12px" />

        <div class="card-list" v-if="rules.length">
          <div v-for="(rule, idx) in rules" :key="idx" class="config-card">
            <div class="card-top">
              <div class="card-main">
                <span class="card-name">{{ rule.name }}</span>
                <span class="card-desc">{{ rule.description }}</span>
              </div>
              <div class="card-meta">
                <el-tag size="small" :type="rule.enabled ? 'success' : 'info'">{{ rule.enabled ? '启用' : '禁用' }}</el-tag>
                <span class="meta-item">优先级 {{ rule.priority }}</span>
                <span class="meta-item">冷却 {{ rule.cooldown_seconds }}s</span>
                <span class="meta-item">{{ rule.conditions.length }} 条件 · {{ rule.actions.length }} 动作</span>
              </div>
              <div class="card-actions">
                <el-button size="small" text @click="openRuleDialog(rule)">编辑</el-button>
                <el-button size="small" text type="danger" @click="deleteRule(idx)">删除</el-button>
              </div>
            </div>
          </div>
        </div>
        <el-empty v-else description="暂无规则" />

        <!-- 规则编辑对话框 -->
        <el-dialog v-model="showRuleDialog" :title="editRule?.name || '新增规则'" width="680px" destroy-on-close>
          <template v-if="editRule">
            <div class="dialog-row">
              <label>名称</label><el-input v-model="editRule.name" size="small" style="width:180px" />
              <label>优先级</label><el-input-number v-model="editRule.priority" size="small" :min="1" :max="200" style="width:90px" />
              <label>冷却(s)</label><el-input-number v-model="editRule.cooldown_seconds" size="small" :min="0" :step="60" style="width:100px" />
              <label>启用</label><el-switch v-model="editRule.enabled" size="small" />
            </div>
            <div class="dialog-row">
              <label>描述</label><el-input v-model="editRule.description" size="small" style="flex:1" />
            </div>

            <h4>条件 (AND)</h4>
            <div v-for="(c, ci) in editRule.conditions" :key="ci" class="cond-row">
              <el-select v-model="c.field" size="small" style="width:90px">
                <el-option v-for="f in FIELD_OPTIONS" :key="f" :label="f" :value="f" />
              </el-select>
              <el-select v-model="c.op" size="small" style="width:100px">
                <el-option v-for="o in OP_OPTIONS" :key="o" :label="o" :value="o" />
              </el-select>
              <template v-if="c.op === 'between' || c.op === 'not_between'">
                <el-input-number v-model="c.min" size="small" style="width:80px" />
                <span>~</span>
                <el-input-number v-model="c.max" size="small" style="width:80px" />
              </template>
              <template v-else>
                <el-input-number v-model="c.value" size="small" style="width:100px" />
              </template>
              <el-button size="small" type="danger" text @click="removeCondition(ci)">✕</el-button>
            </div>
            <el-button size="small" @click="addCondition" style="margin-top:4px">+ 添加条件</el-button>

            <h4>动作</h4>
            <div v-for="(a, ai) in editRule.actions" :key="ai" class="cond-row">
              <el-select v-model="a.device" size="small" style="width:150px">
                <el-option v-for="d in DEVICE_OPTIONS" :key="d" :label="d" :value="d" />
              </el-select>
              <span style="font-size:12px;color:var(--apple-text-tertiary)">模式</span>
              <el-input-number v-model="a.mode" size="small" :min="0" :max="5" style="width:60px" />
              <span style="font-size:12px;color:var(--apple-text-tertiary)">功率W</span>
              <el-input-number v-model="a.power_setpoint" size="small" :min="0" :step="1000" style="width:100px" />
              <span style="font-size:12px;color:var(--apple-text-tertiary)">时效s</span>
              <el-input-number v-model="a.duration" size="small" :min="0" :step="300" style="width:90px" />
              <el-button size="small" type="danger" text @click="removeAction(ai)">✕</el-button>
            </div>
            <el-button size="small" @click="addAction" style="margin-top:4px">+ 添加动作</el-button>
          </template>
          <template #footer>
            <el-button @click="showRuleDialog = false">取消</el-button>
            <el-button type="primary" @click="saveRuleDialog">保存</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <!-- ═══ 告警配置 ═══ -->
      <el-tab-pane label="告警配置" name="alerts">
        <div style="margin-bottom: 12px; display: flex; gap: 8px">
          <el-button size="small" @click="openAlertDialog()">+ 新增告警</el-button>
        </div>

        <el-alert v-if="saveResult && activeTab === 'alerts'" :title="saveResult"
          :type="saveResult.includes('失败') ? 'error' : 'success'" show-icon closable style="margin-bottom:12px" />

        <div class="card-list" v-if="alerts.length">
          <div v-for="(a, idx) in alerts" :key="idx" class="config-card">
            <div class="card-top">
              <div class="card-main">
                <span class="card-name">{{ a.name }}</span>
                <el-tag size="small" :type="a.severity === 'critical' ? 'danger' : 'warning'" style="margin-left:8px">
                  {{ a.severity === 'critical' ? '严重' : '重要' }}
                </el-tag>
                <span class="card-desc" style="margin-left:8px">{{ a.message }}</span>
              </div>
              <div class="card-meta">
                <span class="meta-item">{{ a.device_id }} · {{ a.point }}</span>
                <span class="meta-item">{{ a.op }} {{ a.threshold }}</span>
                <span class="meta-item">冷却 {{ a.cooldown_seconds }}s</span>
              </div>
              <div class="card-actions">
                <el-button size="small" text @click="openAlertDialog(a)">编辑</el-button>
                <el-button size="small" text type="danger" @click="deleteAlert(idx)">删除</el-button>
              </div>
            </div>
          </div>
        </div>
        <el-empty v-else description="暂无告警" />

        <!-- 告警编辑对话框 -->
        <el-dialog v-model="showAlertDialog" :title="editAlert?.name || '新增告警'" width="520px" destroy-on-close>
          <template v-if="editAlert">
            <div class="dialog-row">
              <label>名称</label><el-input v-model="editAlert.name" size="small" style="width:160px" />
              <label>级别</label>
              <el-select v-model="editAlert.severity" size="small" style="width:90px">
                <el-option label="严重" value="critical" />
                <el-option label="重要" value="warning" />
              </el-select>
            </div>
            <div class="dialog-row">
              <label>设备</label><el-input v-model="editAlert.device_id" size="small" style="width:160px" />
              <label>测点</label><el-input v-model="editAlert.point" size="small" style="width:120px" />
            </div>
            <div class="dialog-row">
              <label>条件</label>
              <el-select v-model="editAlert.op" size="small" style="width:70px">
                <el-option v-for="o in ['lt','gt','eq','neq','lte','gte']" :key="o" :label="o" :value="o" />
              </el-select>
              <el-input-number v-model="editAlert.threshold" size="small" style="width:120px" />
              <label>冷却(s)</label>
              <el-input-number v-model="editAlert.cooldown_seconds" size="small" :min="0" :step="60" style="width:100px" />
            </div>
            <div class="dialog-row">
              <label>消息</label><el-input v-model="editAlert.message" size="small" style="flex:1" placeholder="支持 {value} 变量" />
            </div>
          </template>
          <template #footer>
            <el-button @click="showAlertDialog = false">取消</el-button>
            <el-button type="primary" @click="saveAlertDialog">保存</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <!-- ═══ 设备点表 ═══ -->
      <el-tab-pane label="设备点表" name="points">
        <div style="margin-bottom:12px;display:flex;gap:8px">
          <el-button size="small" @click="openPtDialog()">+ 新增设备</el-button>
        </div>
        <el-alert v-if="saveResult && activeTab === 'points'" :title="saveResult"
          :type="saveResult.includes('失败') ? 'error' : 'success'" show-icon closable style="margin-bottom:12px" />
        <el-alert type="info" show-icon :closable="false" style="margin-bottom:16px"
          title="修改点表后需重启采集器生效。register 为 Modbus 0-based 地址，scale 为缩放系数，32bit 寄存器占用连续两个地址。" />

        <div class="card-list" v-if="ptDevices.length">
          <div v-for="(dev, di) in ptDevices" :key="dev.device_id" class="config-card">
            <div class="card-top">
              <div class="card-main">
                <span class="card-name">{{ dev.device_id }}</span>
                <span class="card-desc">slave={{ dev.slave_id }} · 起始={{ dev.read_start }} · 数量={{ dev.read_count }} · {{ dev.points.length }} 个测点</span>
              </div>
              <div class="card-actions">
                <el-button size="small" text @click="openPtDialog(dev)">编辑</el-button>
                <el-button size="small" text type="danger" @click="deletePtDevice(di)">删除</el-button>
              </div>
            </div>
          </div>
        </div>
        <el-empty v-else description="暂无设备点表" />

        <!-- 点表编辑对话框 -->
        <el-dialog v-model="showPtDialog" :title="editPtDevice?.device_id || '新增设备'" width="700px" destroy-on-close>
          <template v-if="editPtDevice">
            <div class="dialog-row">
              <label>设备ID</label><el-input v-model="editPtDevice.device_id" size="small" style="width:160px" />
              <label>Slave</label><el-input-number v-model="editPtDevice.slave_id" size="small" :min="1" :max="247" style="width:80px" />
              <label>起始地址</label><el-input-number v-model="editPtDevice.read_start" size="small" :min="0" style="width:80px" />
              <label>读取数量</label><el-input-number v-model="editPtDevice.read_count" size="small" :min="1" :max="120" style="width:80px" />
            </div>

            <h4>测点定义</h4>
            <div class="pt-header-row">
              <span style="width:120px">名称</span><span style="width:60px">地址</span><span style="width:60px">缩放</span><span style="width:55px">32bit</span><span style="width:55px">有符号</span>
            </div>
            <div v-for="(pt, pi) in editPtDevice.points" :key="pi" class="cond-row">
              <el-input v-model="pt.name" size="small" style="width:120px" placeholder="point_name" />
              <el-input-number v-model="pt.register" size="small" :min="0" style="width:60px" />
              <el-input-number v-model="pt.scale" size="small" :min="0.001" :step="10" style="width:60px" />
              <el-switch v-model="pt.is_32bit" size="small" style="width:55px" />
              <el-switch v-model="pt.is_signed" size="small" style="width:55px" />
              <el-button size="small" type="danger" text @click="removePoint(pi)">✕</el-button>
            </div>
            <el-button size="small" @click="addPoint" style="margin-top:4px">+ 添加测点</el-button>
          </template>
          <template #footer>
            <el-button @click="showPtDialog = false">取消</el-button>
            <el-button type="primary" @click="savePtDialog">保存</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.card-list { display: flex; flex-direction: column; gap: 8px; }

.config-card {
  background: var(--surface-card);
  border-radius: var(--radius-md);
  padding: 16px 20px;
  box-shadow: var(--shadow-subtle);
  transition: box-shadow 0.2s;
}
.config-card:hover { box-shadow: var(--shadow-card); }

.card-top {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.card-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 200px;
}
.card-name {
  font-family: var(--font-display);
  font-size: 15px;
  font-weight: 600;
  color: var(--apple-text);
}
.card-desc {
  font-size: 13px;
  color: var(--apple-text-tertiary);
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-meta {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.meta-item { font-size: 12px; color: var(--apple-text-secondary); }
.card-actions { display: flex; gap: 4px; }

.dialog-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.dialog-row label {
  font-size: 13px;
  font-weight: 600;
  color: var(--apple-text-secondary);
  min-width: 36px;
}
.cond-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.pt-header-row {
  display: flex; gap: 6px; align-items: center; margin-bottom: 4px;
  font-size: 11px; font-weight: 600; color: var(--apple-text-tertiary);
}
h4 {
  font-size: 14px;
  font-weight: 600;
  margin: 14px 0 6px;
  padding-top: 10px;
  border-top: 1px solid rgba(0,0,0,0.06);
}
</style>
