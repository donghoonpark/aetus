import type { DefineComponent } from "vue";

export interface AetusStreamViewerProps {
  queryServerUrl: string;
  deviceId?: string;
  initialStreamKey?: string;
  initialRangePreset?: string;
  maxPointsPerRequest?: number;
}

export const AetusStreamViewer: DefineComponent<AetusStreamViewerProps>;
export default AetusStreamViewer;
