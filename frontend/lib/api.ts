// API + real-time helpers for the cockpit.
//
// - submitShipment: POST /shipments to API Gateway (Cognito-authorized) to
//   classify + start the Step Functions lifecycle via the Orchestrator agent.
// - subscribeToEvents: open an AppSync GraphQL subscription and stream lifecycle
//   domain events to the UI in real time.

import { fetchAuthSession } from "aws-amplify/auth";
import { generateClient } from "aws-amplify/api";
import type { AppConfig } from "./config";

export interface ShipmentRequest {
  origin: string;
  destination: string;
  weight_kg: number;
  commodity: string;
  delivery_date: string;
}

export interface ShipmentEvent {
  event_type: string;
  shipment_id: string;
  timestamp: string;
  step?: string | null;
  model_tier?: string | null;
  confidence?: number | null;
  payload?: unknown;
}

export async function submitShipment(
  config: AppConfig,
  request: ShipmentRequest
): Promise<Record<string, unknown>> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString() ?? "";
  const res = await fetch(`${config.apiUrl}shipments`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: token },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Submit failed (${res.status}): ${text}`);
  }
  return res.json();
}

const ON_SHIPMENT_EVENT = /* GraphQL */ `
  subscription OnShipmentEvent {
    onShipmentEvent {
      event_type
      shipment_id
      timestamp
      step
      model_tier
      confidence
      payload
    }
  }
`;

export function subscribeToEvents(
  onEvent: (e: ShipmentEvent) => void,
  onError?: (err: unknown) => void
): { unsubscribe: () => void } {
  const client = generateClient();
  const sub = client
    .graphql({ query: ON_SHIPMENT_EVENT })
    // @ts-expect-error — subscription returns an Observable
    .subscribe({
      next: (payload: { data?: { onShipmentEvent?: ShipmentEvent } }) => {
        const evt = payload?.data?.onShipmentEvent;
        if (evt) onEvent(evt);
      },
      error: (err: unknown) => onError?.(err),
    });
  return { unsubscribe: () => sub.unsubscribe() };
}
