import { createRouter, createWebHashHistory } from "vue-router";

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: "/",
      name: "Dashboard",
      component: () => import("./views/DashboardView.vue"),
    },
    {
      path: "/devices",
      name: "DeviceList",
      component: () => import("./views/DeviceList.vue"),
    },
    {
      path: "/devices/:id",
      name: "DeviceDetail",
      component: () => import("./views/DeviceDetail.vue"),
      props: true,
    },
    {
      path: "/history/:id",
      name: "HistoryTrend",
      component: () => import("./views/HistoryTrend.vue"),
      props: true,
    },
    {
      path: "/control",
      name: "ControlPanel",
      component: () => import("./views/ControlPanel.vue"),
    },
    {
      path: "/forecast",
      name: "ForecastView",
      component: () => import("./views/ForecastView.vue"),
    },
    {
      path: "/alerts",
      name: "AlertView",
      component: () => import("./views/AlertView.vue"),
    },
  ],
});

export default router;
