<script setup lang="ts">
import { useRoute } from "vue-router";
const route = useRoute();
</script>

<template>
  <nav class="sidebar">
    <div class="sidebar-logo">IES</div>
    <div class="sidebar-links">
      <router-link to="/" class="nav-item" :class="{ active: route.path === '/' }">
        驾驶舱
      </router-link>
      <router-link to="/devices" class="nav-item" :class="{ active: route.path.startsWith('/devices') }">
        设备
      </router-link>
      <router-link to="/control" class="nav-item" :class="{ active: route.path === '/control' }">
        控制
      </router-link>
      <router-link to="/forecast" class="nav-item" :class="{ active: route.path === '/forecast' }">
        预测
      </router-link>
      <router-link to="/alerts" class="nav-item" :class="{ active: route.path === '/alerts' }">
        告警
      </router-link>
      <router-link to="/settings" class="nav-item" :class="{ active: route.path === '/settings' }">
        配置
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
  text-decoration: none;
}
.nav-item.active {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.14);
  font-weight: 500;
}
</style>
