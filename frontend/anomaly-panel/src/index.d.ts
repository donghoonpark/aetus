import type { DefineComponent } from "vue";

export interface AetusAnomalyPanelProps {
  anomalyServerUrl: string;
  authToken?: string;
  autoRefreshMs?: number;
}

declare const AetusAnomalyPanel: DefineComponent<AetusAnomalyPanelProps>;
export { AetusAnomalyPanel };
export default AetusAnomalyPanel;
