/**
 * 前端全局配置。
 *
 * API_BASE — REST API 基地址。
 *   - 本地开发：Vite 默认注入 "http://localhost:8000"
 *   - Docker 构建：VITE_API_BASE=""（nginx 反向代理 /api → backend:8000）
 *
 * WS_URL — WebSocket 连接地址，从 API_BASE 自动推导。
 */

const base = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export const API_BASE = base;

export const WS_URL = base
  ? base.replace(/^http/, "ws") + "/api/ws/realtime"
  : `ws://${window.location.host}/api/ws/realtime`;
