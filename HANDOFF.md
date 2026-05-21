# IES 多能流协同控制终端 — 工作交接文档

> 生成时间：2026-05-21
> 最后 commit：`f54c073` fix: CSV export multi-column alignment + reports UI redesign as 3-column cards

---

## 一、项目概览

面向综合能源/零碳园区的边缘侧协同控制终端。覆盖电、冷、热、气四种能源品类，完成"数据采集 → 运行分析 → 预测 + 规则控制 → 控制下发"闭环。

**核心指标：**
- 8 台模拟设备（5 Modbus TCP + 3 MQTT），涵盖光伏、储能、CHP、热泵、蓄能、电表、环境传感器
- 采集频率 5 秒，15 分钟预测周期，30 秒规则评估周期，15 秒告警评估周期
- 前端 9 个页面，Apple 设计系统（暗色侧边栏 + 黑白分区布局）

---

## 二、技术架构

```
simulator/                    backend/                     frontend/
(模拟设备层)                   (FastAPI 后端)              (Vue 3 前端)
                               
device_simulator.py ──Modbus──→ collector.py ──→ TDengine     Vue Router
  (5 devices, slaves 1-5)        (5s polling)       REST API    9 views
                               
mqtt_simulator.py ────MQTT───→ mqtt_collector.py           Element Plus
  (3 devices)                    (subscribe)                ECharts 6

                               rule_engine.py  (30s)
                               alert_engine.py (15s)
                               forecast_pv.py  (900s)
                               point_table.py  (config)
```

**通信协议：**
- Modbus TCP：端口 5020（模拟器），slave ID 1-5，32-bit big-endian 编码
- MQTT：端口 1883，topic `ies/device/{device_id}/telemetry`，JSON payload
- TDengine REST API：端口 6041，`POST /rest/sql/{db}`，Basic auth

**关键依赖版本：**
- pymodbus==3.7.4（必须此版本，3.13 API 不兼容）
- TDengine 3.3.3.0 (Docker)
- Eclipse Mosquitto 2 (Docker)
- Vue 3.5 + ECharts 6 + Element Plus 2.14
- Python 3.13 + FastAPI 0.115

---

## 三、项目文件结构

```
IES-Control-Hardware-Software/
├── HANDOFF.md                 ← 本文件
├── DESIGN.md                  ← Apple 设计系统规范（颜色/字体/组件）
├── .gitignore
│
├── docker/
│   ├── docker-compose.yml     ← TDengine + Mosquitto
│   └── mosquitto.conf         ← 匿名访问，端口 1883 + 9001(WS)
│
├── simulator/                 # 设备模拟器（独立进程，先启动）
│   ├── device_simulator.py   ← 5 Modbus 设备模拟
│   ├── mqtt_simulator.py     ← 3 MQTT 设备模拟
│   └── requirements.txt
│
├── backend/                   # FastAPI 后端
│   ├── requirements.txt
│   └── app/
│       ├── main.py            ← FastAPI 入口，lifespan 管理服务生命周期
│       ├── api/
│       │   └── routes.py      ← 全部 API 路由（~580 行）
│       ├── core/
│       │   ├── config.py      ← 配置（TDengine/Modbus/采集间隔等）
│       │   └── tdengine.py    ← TDengine REST 客户端（SQL 执行/遥测写入）
│       └── services/
│           ├── collector.py   ← Modbus 采集器（基于 point_tables.json 动态轮询）
│           ├── mqtt_collector.py ← MQTT 采集器（订阅 telemetry 主题）
│           ├── rule_engine.py ← 规则引擎（条件评估 → 动作执行）
│           ├── alert_engine.py ← 告警引擎（阈值 + 冷却 + 自恢复）
│           ├── forecast_pv.py ← 光伏预测（Ineichen-Perez 晴空模型）+ 负荷预测
│           ├── point_table.py ← 设备点表配置管理
│           ├── point_tables.json ← 5 设备完整寄存器映射
│           ├── rules.json     ← 规则配置（可热加载）
│           └── alerts.json    ← 告警配置（可热加载）
│
├── frontend/                  # Vue 3 前端
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   ├── dist/                  ← 构建产物
│   └── src/
│       ├── main.ts
│       ├── App.vue
│       ├── style.css          ← Apple 设计 Tokens + Element Plus 全局覆盖
│       ├── router.ts          ← 9 条路由
│       ├── types.ts
│       ├── components/
│       │   └── Sidebar.vue    ← 毛玻璃导航栏（220px 宽，sticky）
│       └── views/
│           ├── DashboardView.vue    ← 驾驶舱：KPI + 桑基能量流 + 设备概况
│           ├── DeviceList.vue       ← 设备列表：卡片网格
│           ├── DeviceDetail.vue     ← 设备详情：遥测表 + 仪表盘 + 控制
│           ├── ControlPanel.vue     ← 控制面板：设备专用控制指令下发
│           ├── HistoryTrend.vue     ← 历史趋势：曲线 + 数据缩放
│           ├── ForecastView.vue     ← 预测：PV/负荷曲线 + 规则执行状态
│           ├── AlertView.vue        ← 告警：统计 + 筛选 + 确认
│           ├── SettingsView.vue     ← 配置：规则/告警/点表在线编辑
│           └── ReportsView.vue      ← 报表：日报/月报/CSV 三栏卡片
│
└── venv/                      # Python 虚拟环境（gitignore）
```

---

## 四、如何启动

### 前置条件
- Docker Desktop 已安装运行
- Python 3.13 + Node.js 22
- 项目根目录下 venv 已创建，依赖已安装
- `frontend/node_modules` 已安装

### 启动顺序（必须按此顺序）

```bash
# 1. 启动基础设施（TDengine + Mosquitto）
cd docker
docker compose up -d
# 等待 TDengine 健康检查通过（docker ps → healthy）

# 2. 启动设备模拟器（两个进程）
cd simulator
python device_simulator.py &    # Modbus TCP :5020
python mqtt_simulator.py &      # MQTT :1883

# 3. 启动后端
cd backend
uvicorn app.main:app --reload --port 8000

# 4. 启动前端开发服务器
cd frontend
npm run dev    # Vite → http://localhost:5173
```

### 首次运行或数据库重建时
后端启动后会自动创建 TDengine 数据库和超级表。模拟器持续运行即可产生数据。

### 修改后端配置
环境变量（可选，都有默认值）：
```bash
export IES_TD_HOST=localhost
export IES_TD_PORT=6030
export IES_MODBUS_HOST=localhost
export IES_MODBUS_PORT=5020
export IES_COLLECT_INTERVAL=5
```

---

## 五、关键踩坑记录（非常重要）

### 1. pymodbus 版本锁定
**必须使用 pymodbus==3.7.4**。3.13 版本 API 彻底变化：
- `ModbusSlaveContext` → `ModbusDeviceContext`
- `context[slave_id].store["h"]` 访问方式改变
- 寄存器地址 0-based vs 1-based 行为不同

### 2. TDengine REST API
- REST 端口是 **6041**（不是 6030，6030 是原生连接端口）
- 请求头必须有 `Content-Type: application/x-www-form-urlencoded`
- httpx 客户端必须 `trust_env=False, http1=True`（否则 macOS 系统代理会拦截 localhost 流量返回 502）
- 不支持 `GROUP BY CAST(ts AS BIGINT) / 86400000` 这样的表达式（月报改为逐日循环查询）
- Schemaless insert (`/rest/schemaless`) 是 WebSocket 端点，用 `INSERT INTO` SQL 替代

### 3. 模拟器命令过期机制
控制指令过期后必须同时清零寄存器 52（mode）和 54（duration），否则下一个循环会把旧值当新指令重新执行。代码在 `device_simulator.py` 的 `update()` 方法中通过 `effective_until` 字段检查实现。

### 4. 32-bit 寄存器编码
控制寄存器 50-51 是 32-bit power setpoint（大端序），始终用 2 个寄存器。之前的 bug 是当 `len(values)==1` 走了 16-bit 单寄存器路径，导致 PV 限功率失效。

### 5. 桑基图动态生成
`/api/dashboard/overview` 中的 `energy_flow` 根据实际设备运行数据动态生成流向链接，包含电、热、冷三类能源流。CHP 发电和余热分别建模。

### 6. 前端的 API_BASE 硬编码
多个 Vue 文件中 `API_BASE = "http://localhost:8000"` 是硬编码的，部署时需要统一管理（使用环境变量或 Vite proxy）。

---

## 六、已完成模块清单

| 模块 | 关键文件 | 完成度 |
|------|----------|--------|
| 设备模拟器 (5 Modbus) | `simulator/device_simulator.py` | ✅ 完整（含控制指令、过期机制） |
| 设备模拟器 (3 MQTT) | `simulator/mqtt_simulator.py` | ✅ 完整 |
| TDengine 时序库 | `backend/app/core/tdengine.py` | ✅ 完整（建表/写入/查询） |
| Modbus 采集器 | `backend/app/services/collector.py` | ✅ 完整（动态点表驱动） |
| MQTT 采集器 | `backend/app/services/mqtt_collector.py` | ✅ 完整 |
| REST API（设备/控制/报表/驾驶舱） | `backend/app/api/routes.py` | ✅ 完整（~580 行） |
| WebSocket 实时推送 | `backend/app/api/routes.py` → `/ws/realtime` | ✅ 完整 |
| 规则引擎 | `backend/app/services/rule_engine.py` | ✅ 完整（条件评估/动作执行/热加载） |
| 告警引擎 | `backend/app/services/alert_engine.py` | ✅ 完整（阈值/冷却/自恢复/确认） |
| 光伏预测 | `backend/app/services/forecast_pv.py` | ✅ 基线版本（晴空模型） |
| 负荷预测 | `backend/app/services/forecast_pv.py` | ✅ 基线版本（历史均值） |
| 设备点表配置 | `backend/app/services/point_table.py` | ✅ 完整（JSON 文件驱动） |
| 驾驶舱页面 | `frontend/src/views/DashboardView.vue` | ✅ 完整（KPI + 桑基图 + 设备概况） |
| 设备列表 | `frontend/src/views/DeviceList.vue` | ✅ 完整（8 设备卡片网格） |
| 设备详情 | `frontend/src/views/DeviceDetail.vue` | ✅ 完整（数据驱动，8 种设备类型） |
| 控制面板 | `frontend/src/views/ControlPanel.vue` | ✅ 完整（设备专用控制模式） |
| 历史趋势 | `frontend/src/views/HistoryTrend.vue` | ✅ 完整 |
| 预测页面 | `frontend/src/views/ForecastView.vue` | ✅ 完整 |
| 告警页面 | `frontend/src/views/AlertView.vue` | ✅ 完整（统计/筛选/确认） |
| 配置页面 | `frontend/src/views/SettingsView.vue` | ✅ 完整（规则/告警/点表在线编辑） |
| 报表页面 | `frontend/src/views/ReportsView.vue` | ✅ 完整（日报/月报/CSV 三栏） |
| Apple 设计系统 | `DESIGN.md` + `frontend/src/style.css` | ✅ 完整（SF Pro / 黑白分区 / 毛玻璃） |

---

## 七、Git 历史

```
f54c073 fix: CSV export multi-column alignment + reports UI redesign as 3-column cards
4cd6105 feat: data report export (daily/monthly stats + CSV download)
ac391ad feat: online config system + dynamic point table
b91c3bd Initial commit: IES Control Terminal
```

Remote: `https://github.com/lly102799-git/IES-Control-Hardware-Software.git`  
Auth: `gh auth setup-git` 可用 GitHub CLI 管理凭据

---

## 八、接下来的工作计划

### 第一优先级：系统健壮化

#### 1. Docker 一键部署
- **目标**：`docker compose up` 一条命令启动全部服务（TDengine + Mosquitto + 后端 + 前端 + 模拟器）
- **当前状态**：docker-compose.yml 只有 TDengine + Mosquitto，后端和前端需要手动启动
- **工作内容**：
  - 为后端写 Dockerfile（Python FastAPI）
  - 为前端写 Dockerfile（多阶段构建：build → nginx 托管 dist）
  - 为模拟器写 Dockerfile
  - 更新 docker-compose.yml 编排 5 个服务，配置健康检查和启动依赖顺序
  - 前端 API_BASE 改为可配置（构建时环境变量 `VITE_API_BASE`，nginx 反向代理到后端）
- **关键文件**：`docker/docker-compose.yml`, 新建 `docker/Dockerfile.backend`, `docker/Dockerfile.frontend`, `docker/Dockerfile.simulator`

#### 2. 数据生命周期管理
- **目标**：防止边缘侧磁盘写满
- **方案**：TDengine 自动删除策略（`INTERVAL` + `KEEP`），热数据 7 天、温数据 90 天
- **工作内容**：修改 TDengine 建库 SQL 添加 `KEEP 90d`，超级表按需设置 `KEEP` 参数

#### 3. 错误处理补全
- **目标**：后端全局异常中间件（统一错误响应格式），前端统一错误提示（替代裸 `console.error`）
- **工作内容**：后端添加 `@app.exception_handler`，前端封装 `useFetch` 或 Axios interceptor

### 第二优先级：算法增强

#### 4. 场景仿真测试脚本
- **目标**：构造典型日运行场景，验证全链路表现
- **场景**：
  - 晴天工作日：光伏满发 + 负荷正常 + 储能峰谷套利
  - 阴天周末：光伏低发 + 负荷低 + CHP 主导供热
  - 电网故障切换：模拟电网失电 → 规则引擎自动离网模式
  - 极端负荷日：夏季制冷高峰 / 冬季供热高峰
- **方案**：编写 Python 脚本，修改模拟器参数（天气因子、负荷系数）来驱动不同场景，收集后端日志和 TDengine 数据验证规则触发情况
- **关键文件**：新建 `tests/scenario_test.py`

#### 5. 设备动态建模
- **目标**：建立关键设备的稳态数学模型，为后续 MILP 优化提供约束参数
- **工作内容**：
  - 光伏：功率 = f(GHI, 温度, 面板参数)，与 forecast_pv.py 中的模型对齐
  - 储能：SOC 递推 = f(充放电功率, 效率, 容量)
  - CHP：热电耦合关系（电效率 0.35，总效率 0.85）
  - 热泵：COP = f(源温, 出水温)，已有 COP 曲线数据
  - 蓄能罐：蓄/放能功率 → SOC 变化
- **关键文件**：新建 `backend/app/services/device_models.py`

#### 6. 预测模块升级（数据驱动）
- **前提**：需要积累至少 2-4 周历史数据
- **方案**：当前物理晴空模型 → 数据足够后切换到 LightGBM
- **工作内容**：从 TDengine 提取历史数据训练 LightGBM 模型，对比物理模型基线

### 第三优先级：前端完善

#### 7. 响应式适配
- **目标**：适配现场调试用 Pad（1024×768 以上）
- **工作内容**：侧边栏折叠、卡片网格断点调整、图表尺寸响应式

#### 8. 离线状态指示
- **目标**：WebSocket 断连时前端展示最后已知状态 + 重连提示
- **工作内容**：`useWebSocket` composable，断线 UI 提示

### 延后事项（不在此 session 处理）

- **MILP 优化引擎**：用户明确延后
- **硬件设计 / 嵌入式 BSP**：需要核心板到位
- **IEC 104 协议**：Modbus + MQTT 已覆盖当前全部设备
- **用户认证 JWT**：单机边缘终端暂不需要
- **OTA 升级**：需要硬件平台

---

## 九、下一步建议

**推荐启动顺序**：Docker 打包 → 场景仿真测试 → 设备建模 → 预测升级

> **理由**：Docker 打包让系统可脱离开发环境演示和验证；场景仿真能发现规则引擎在边界条件下的表现问题；设备建模为 MILP 优化做准备。

---

## 十、有用命令速查

```bash
# 基础设施
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml down

# 模拟器
cd simulator && python device_simulator.py &
cd simulator && python mqtt_simulator.py &

# 后端
cd backend && uvicorn app.main:app --reload --port 8000

# 前端
cd frontend && npm run dev     # http://localhost:5173
cd frontend && npm run build   # 构建到 dist/

# TDengine 查询
curl -u root:taosdata "http://localhost:6041/rest/sql/ies" \
  -d "SELECT last(*) FROM telemetry GROUP BY point_name"

# 直接读 Modbus 寄存器（验证模拟器运行状态）
python -c "
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient('127.0.0.1', port=5020)
c.connect()
rr = c.read_holding_registers(0, 10, slave=1)  # slave 1 = PV inverter
print(rr.registers)
c.close()
"

# Git 操作（使用 gh 认证）
git -c credential.helper='!gh auth git-credential' push
```
