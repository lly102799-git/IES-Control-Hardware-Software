<script setup lang="ts">
import { WS_URL } from "./config";
import { useOnline } from "./composables/useOnline";
import Sidebar from "./components/Sidebar.vue";

const { online, lastData, reconnect } = useOnline(WS_URL);
</script>

<template>
  <div class="app-layout">
    <!-- 离线指示条 -->
    <Transition name="offline-fade">
      <div v-if="!online" class="offline-bar">
        <span>连接已断开 — 显示最后已知状态</span>
        <span v-if="lastData" style="opacity:0.7;margin-left:12px">
          ({{ new Date().toLocaleTimeString("zh-CN", { hour12: false }) }})
        </span>
        <button class="offline-retry" @click="reconnect">重新连接</button>
      </div>
    </Transition>

    <Sidebar />
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
}
.app-main {
  flex: 1;
  overflow-y: auto;
  background: #f0f2f5;
}
.offline-bar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 9999;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 500;
  color: #fff;
  background: #ff3b30;
}
.offline-retry {
  margin-left: 16px;
  padding: 2px 12px;
  border: 1px solid rgba(255,255,255,0.5);
  border-radius: 4px;
  background: transparent;
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}
.offline-retry:hover { background: rgba(255,255,255,0.15); }
.offline-fade-enter-active, .offline-fade-leave-active { transition: opacity 0.3s; }
.offline-fade-enter-from, .offline-fade-leave-to { opacity: 0; }

/* ── 响应式 ── */
@media (max-width: 1024px) {
  .app-layout { flex-direction: column; }
}
</style>
