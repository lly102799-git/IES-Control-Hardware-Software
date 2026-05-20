export interface Device {
  device_id: string;
  name: string;
  dev_type: string;
  rated_power_w: number;
  capacity_kwh?: number;
  status: string;
}

export interface TelemetryPoint {
  ts: string;
  val: number;
  quality: number;
}
