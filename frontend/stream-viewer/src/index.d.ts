import type { DefineComponent } from "vue";

export interface AetusStreamViewerProps {
  queryServerUrl: string;
  authToken?: string;
  tokenProvider?: () => string | Promise<string>;
  deviceId?: string;
  initialDeviceIds?: string[];
  initialStreamKey?: string;
  initialRangePreset?: string;
  maxPointsPerRequest?: number;
  autoOpenControls?: boolean;
  panelTitle?: string;
}

export const AetusStreamViewer: DefineComponent<AetusStreamViewerProps>;
export default AetusStreamViewer;
