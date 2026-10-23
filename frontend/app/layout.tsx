import type { ReactNode } from "react";

export const metadata = {
  title: "TMS Transportation Cockpit",
  description: "Cost-optimized agentic TMS on Amazon Bedrock AgentCore",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
          background: "#0f1720",
          color: "#e6edf3",
        }}
      >
        {children}
      </body>
    </html>
  );
}
