<script setup lang="ts">
import Sidebar from "./components/Sidebar.vue";
</script>

<template>
  <div class="app-layout">
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
</style>
