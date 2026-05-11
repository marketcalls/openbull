/**
 * Types for the Playground WebSocket tester.
 *
 * Mirrors the openalgo equivalents so ported components compile without
 * renaming. Direction taxonomy is the same — sent / received / error /
 * system — which the MessageLog renders with colour-coded badges.
 */

export type MessageDirection = "sent" | "received" | "error" | "system";

export interface WebSocketMessage {
  id: string;
  direction: MessageDirection;
  timestamp: number;
  data: unknown;
  /** Raw JSON-encoded form, used for the syntax-highlighted preview. */
  rawData?: string;
}

export interface MessageTemplate {
  key: string;
  label: string;
  description: string;
  template: Record<string, unknown>;
}

export interface LatencySample {
  timestamp: number;
  latency: number;
}
