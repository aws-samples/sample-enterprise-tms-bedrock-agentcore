"use client";

import { useEffect, useMemo, useState } from "react";
import { loadConfig, type AppConfig } from "../lib/config";
import { configureAmplify } from "../lib/amplify";
import {
  submitShipment,
  subscribeToEvents,
  type ShipmentEvent,
  type ShipmentRequest,
} from "../lib/api";
import { signIn, signOut, getCurrentUser } from "aws-amplify/auth";

const DEFAULT_REQUEST: ShipmentRequest = {
  origin: "Chicago, IL",
  destination: "New York, NY",
  weight_kg: 2500,
  commodity: "Electronics",
  delivery_date: "2026-02-15",
};

const LIFECYCLE = [
  "ShipmentCreated",
  "RouteOptimized",
  "CarrierAssigned",
  "ExceptionDetected",
  "ExceptionResolved",
  "ChargesCalculated",
  "ShipmentCompleted",
];

const AI_STEPS = new Set(["RouteOptimized", "ExceptionResolved"]);

// UI copy kept in one place. Centralizing display strings here is the seam a
// future i18n layer (e.g. i18next) would plug into; it also keeps literal text
// out of the JSX tree.
const UI_TEXT = {
  signInPrompt: "Sign in to continue",
  signIn: "Sign in",
  signOut: "Sign out",
  lifecycleProgress: "Lifecycle progress",
  summary: "Summary",
  summaryLine: (slm: number, llm: number, det: number) =>
    `SLM-handled AI steps: ${slm} · LLM escalations: ${llm} · Zero-AI-cost steps: ${det}`,
} as const;

export default function Cockpit() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [authed, setAuthed] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [events, setEvents] = useState<ShipmentEvent[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState("");

  // Load runtime config + configure Amplify once.
  useEffect(() => {
    loadConfig()
      .then((c) => {
        configureAmplify(c);
        setConfig(c);
        return getCurrentUser().then(() => setAuthed(true)).catch(() => {});
      })
      .catch((e) => setStatus(`Config error: ${e.message}`));
  }, []);

  // Subscribe to live lifecycle events once authenticated.
  useEffect(() => {
    if (!authed || !config) return;
    const sub = subscribeToEvents(
      (evt) => setEvents((prev) => dedupe([...prev, evt])),
      (err) => setStatus(`Realtime error: ${String(err)}`)
    );
    return () => sub.unsubscribe();
  }, [authed, config]);

  const summary = useMemo(() => summarize(events), [events]);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setAuthError("");
    try {
      await signIn({ username: email, password });
      setAuthed(true);
    } catch (err) {
      setAuthError(String((err as Error).message ?? err));
    }
  }

  async function handleSubmit() {
    if (!config) return;
    setSubmitting(true);
    setStatus("Classifying request via Orchestrator agent (SLM-first)…");
    setEvents([]);
    try {
      const result = await submitShipment(config, DEFAULT_REQUEST);
      setStatus(
        `Workflow started — classification: ${result.classification ?? "n/a"}, ` +
          `model: ${result.model_tier ?? "n/a"}`
      );
    } catch (err) {
      setStatus(`Submit failed: ${String((err as Error).message ?? err)}`);
    } finally {
      setSubmitting(false);
    }
  }

  if (!config) {
    return <Centered>{status || "Loading configuration…"}</Centered>;
  }

  if (!authed) {
    return (
      <Centered>
        <form onSubmit={handleLogin} style={{ width: 320 }}>
          <h1 style={{ fontSize: 22 }}>🚛 TMS Cockpit</h1>
          <p style={{ opacity: 0.7, fontSize: 14 }}>{UI_TEXT.signInPrompt}</p>
          <input
            placeholder="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={inputStyle}
          />
          <input
            placeholder="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={inputStyle}
          />
          <button type="submit" style={primaryBtn}>
            {UI_TEXT.signIn}
          </button>
          {authError && (
            <p style={{ color: "#ff7b72", fontSize: 13 }}>{authError}</p>
          )}
        </form>
      </Centered>
    );
  }

  return (
    <main style={{ maxWidth: 900, margin: "0 auto", padding: 24 }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <h1 style={{ fontSize: 24 }}>🚛 TMS Transportation Cockpit</h1>
        <button
          onClick={() => signOut().then(() => setAuthed(false))}
          style={ghostBtn}
        >
          {UI_TEXT.signOut}
        </button>
      </header>
      <p style={{ opacity: 0.7 }}>
        3 AI agents on Amazon Bedrock AgentCore with a Step Functions
        deterministic backbone. AI only where reasoning is needed.
      </p>

      <button onClick={handleSubmit} disabled={submitting} style={primaryBtn}>
        {submitting ? "Submitting…" : "🚀 Submit Shipment"}
      </button>
      {status && <p style={{ fontSize: 14, opacity: 0.85 }}>{status}</p>}

      <section style={{ marginTop: 24 }}>
        <h2 style={{ fontSize: 18 }}>{UI_TEXT.lifecycleProgress}</h2>
        {LIFECYCLE.map((step) => {
          const evt = events.find((e) => e.event_type === step);
          return (
            <div key={step} style={rowStyle(!!evt)}>
              <span>{evt ? "✅" : "⬜"} {step}</span>
              {evt && AI_STEPS.has(step) && (
                <span style={{ fontSize: 13, opacity: 0.8 }}>
                  {evt.model_tier}{" "}
                  {evt.confidence != null && `· conf ${evt.confidence.toFixed(2)}`}
                </span>
              )}
            </div>
          );
        })}
      </section>

      {events.length > 0 && (
        <section style={{ marginTop: 24 }}>
          <h2 style={{ fontSize: 18 }}>{UI_TEXT.summary}</h2>
          <p style={{ fontSize: 14 }}>
            {UI_TEXT.summaryLine(summary.slm, summary.llm, summary.deterministic)}
          </p>
        </section>
      )}
    </main>
  );
}

function summarize(events: ShipmentEvent[]) {
  let slm = 0;
  let llm = 0;
  let deterministic = 0;
  for (const e of events) {
    if (AI_STEPS.has(e.event_type)) {
      if (e.model_tier === "SLM") slm++;
      else if (e.model_tier === "LLM") llm++;
    } else {
      deterministic++;
    }
  }
  return { slm, llm, deterministic };
}

function dedupe(events: ShipmentEvent[]): ShipmentEvent[] {
  const seen = new Set<string>();
  const out: ShipmentEvent[] = [];
  for (const e of events) {
    const key = `${e.shipment_id}:${e.event_type}`;
    if (!seen.has(key)) {
      seen.add(key);
      out.push(e);
    }
  }
  return out;
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {children}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  padding: 10,
  margin: "8px 0",
  borderRadius: 6,
  border: "1px solid #30363d",
  background: "#0d1117",
  color: "#e6edf3",
  boxSizing: "border-box",
};

const primaryBtn: React.CSSProperties = {
  padding: "12px 20px",
  borderRadius: 8,
  border: "none",
  background: "#238636",
  color: "white",
  fontSize: 15,
  cursor: "pointer",
  marginTop: 12,
};

const ghostBtn: React.CSSProperties = {
  padding: "8px 14px",
  borderRadius: 6,
  border: "1px solid #30363d",
  background: "transparent",
  color: "#e6edf3",
  cursor: "pointer",
};

function rowStyle(done: boolean): React.CSSProperties {
  return {
    display: "flex",
    justifyContent: "space-between",
    padding: "10px 14px",
    margin: "6px 0",
    borderRadius: 6,
    background: done ? "#12261a" : "#161b22",
    border: `1px solid ${done ? "#238636" : "#30363d"}`,
  };
}
