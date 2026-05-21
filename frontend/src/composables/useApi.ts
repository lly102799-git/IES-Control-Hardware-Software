/**
 * 统一 API 请求封装。替代散布在各页面的裸 fetch + console.error。
 *
 * 用法：
 *   const { data, loading, error, get, post } = useApi()
 *   await get('/api/devices')
 *   await post('/api/devices/battery_pcs_01/command', { mode: 2, ... })
 */

import { ref } from "vue";
import { API_BASE } from "../config";

export function useApi() {
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function request<T = any>(
    method: string,
    path: string,
    body?: any
  ): Promise<T | null> {
    loading.value = true;
    error.value = null;
    try {
      const opts: RequestInit = {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      };
      const res = await fetch(`${API_BASE}${path}`, opts);
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(`HTTP ${res.status}: ${txt.slice(0, 200)}`);
      }
      return await res.json();
    } catch (e: any) {
      error.value = e.message || String(e);
      console.error(`[API ${method}] ${path}:`, e.message || e);
      return null;
    } finally {
      loading.value = false;
    }
  }

  function get<T = any>(path: string) { return request<T>("GET", path); }
  function post<T = any>(path: string, body?: any) { return request<T>("POST", path, body); }

  return { loading, error, get, post };
}
