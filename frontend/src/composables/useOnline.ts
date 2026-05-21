/**
 * WebSocket 连接状态监听。用于离线指示和自动重连。
 *
 * 用法：
 *   const { online, lastData, reconnect } = useOnline(wsUrl)
 *   watch(online, (v) => { if (!v) showBanner() })
 */

import { ref, onUnmounted } from "vue";

export function useOnline(wsUrl: string) {
  const online = ref(true);
  const lastData = ref<Record<string, any> | null>(null);
  let ws: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let pingTimer: number | null = null;

  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN) return;
    try {
      ws = new WebSocket(wsUrl);
      ws.onopen = () => {
        online.value = true;
        if (pingTimer) clearInterval(pingTimer);
        pingTimer = window.setInterval(() => {
          if (ws?.readyState === WebSocket.OPEN) ws.send("ping");
        }, 30000);
      };
      ws.onmessage = (ev) => {
        try { lastData.value = JSON.parse(ev.data); } catch { /* */ }
      };
      ws.onclose = () => {
        online.value = false;
        if (pingTimer) clearInterval(pingTimer);
        scheduleReconnect();
      };
      ws.onerror = () => {
        online.value = false;
        ws?.close();
      };
    } catch {
      online.value = false;
      scheduleReconnect();
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(connect, 5000);
  }

  function reconnect() {
    if (ws) { ws.close(); ws = null; }
    connect();
  }

  connect();

  onUnmounted(() => {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (pingTimer) clearInterval(pingTimer);
    if (ws) { ws.close(); ws = null; }
  });

  return { online, lastData, reconnect };
}
