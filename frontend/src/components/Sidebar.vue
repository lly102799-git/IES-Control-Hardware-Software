<script setup lang="ts">
import { ref } from "vue";
import { useRoute } from "vue-router";
const route = useRoute();
const collapsed = ref(true);
</script>

<template>
  <!-- 移动端 hamburger -->
  <button class="hamburger" @click="collapsed = !collapsed" :title="collapsed ? '展开菜单' : '收起菜单'">
    <span :class="{ open: !collapsed }" />
  </button>

  <!-- 侧边栏 -->
  <nav class="sidebar" :class="{ collapsed }">
    <div class="sidebar-logo">IES</div>
    <div class="sidebar-links">
      <router-link to="/" class="nav-item" :class="{ active: route.path === '/' }" @click="collapsed = true">
        驾驶舱
      </router-link>
      <router-link to="/devices" class="nav-item" :class="{ active: route.path.startsWith('/devices') }" @click="collapsed = true">
        设备
      </router-link>
      <router-link to="/control" class="nav-item" :class="{ active: route.path === '/control' }" @click="collapsed = true">
        控制
      </router-link>
      <router-link to="/forecast" class="nav-item" :class="{ active: route.path === '/forecast' }" @click="collapsed = true">
        预测
      </router-link>
      <router-link to="/alerts" class="nav-item" :class="{ active: route.path === '/alerts' }" @click="collapsed = true">
        告警
      </router-link>
      <router-link to="/settings" class="nav-item" :class="{ active: route.path === '/settings' }" @click="collapsed = true">
        配置
      </router-link>
      <router-link to="/reports" class="nav-item" :class="{ active: route.path === '/reports' }" @click="collapsed = true">
        报表
      </router-link>
      <router-link to="/monitor" class="nav-item" :class="{ active: route.path === '/monitor' }" @click="collapsed = true">
        监控
      </router-link>
    </div>
  </nav>
</template>

<style scoped>
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  width: 220px;
  min-width: 220px;
  display: flex;
  flex-direction: column;
  background: rgba(29, 29, 31, 0.92);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  z-index: 100;
  padding: 20px 0;
  transition: transform 0.25s ease;
}
.sidebar-logo {
  font-family: 'SF Pro Display', -apple-system, 'Helvetica Neue', sans-serif;
  font-size: 21px;
  font-weight: 700;
  color: #ffffff;
  padding: 8px 28px 24px;
  letter-spacing: 0.231px;
}
.sidebar-links {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 12px;
}
.nav-item {
  display: block;
  padding: 8px 16px;
  font-size: 14px;
  font-weight: 400;
  letter-spacing: -0.224px;
  color: rgba(255, 255, 255, 0.72);
  text-decoration: none;
  border-radius: 8px;
  transition: all 0.2s ease;
}
.nav-item:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.08);
}
.nav-item.active {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.14);
  font-weight: 500;
}

/* ── 响应式 ── */
.hamburger {
  display: none;
  position: fixed;
  top: 12px;
  left: 12px;
  z-index: 200;
  width: 32px;
  height: 32px;
  border: none;
  background: rgba(29,29,31,0.9);
  border-radius: 6px;
  cursor: pointer;
  padding: 6px;
}
.hamburger span {
  display: block;
  width: 20px;
  height: 2px;
  background: #fff;
  border-radius: 1px;
  position: relative;
  transition: background 0.2s;
}
.hamburger span::before, .hamburger span::after {
  content: "";
  position: absolute;
  left: 0;
  width: 20px;
  height: 2px;
  background: #fff;
  border-radius: 1px;
  transition: transform 0.25s;
}
.hamburger span::before { top: -6px; }
.hamburger span::after { top: 6px; }
.hamburger span.open { background: transparent; }
.hamburger span.open::before { transform: translateY(6px) rotate(45deg); }
.hamburger span.open::after { transform: translateY(-6px) rotate(-45deg); }

@media (max-width: 1024px) {
  .hamburger { display: block; }
  .sidebar {
    position: fixed;
    left: 0;
    top: 0;
    transform: translateX(-100%);
  }
  .sidebar:not(.collapsed) { transform: translateX(0); }
}
</style>
