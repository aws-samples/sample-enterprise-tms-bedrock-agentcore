// Runtime configuration loader.
//
// The static export is deployed to S3/CloudFront without deploy-time values
// baked in. Instead, the CDK stack writes a `config.json` into the hosting
// bucket at deploy time with the real API Gateway URL, AppSync URL, and Cognito
// IDs. The app fetches it once at startup.

export interface AppConfig {
  region: string;
  apiUrl: string; // API Gateway base URL (POST {apiUrl}shipments)
  realtimeApiUrl: string; // AppSync GraphQL endpoint
  userPoolId: string;
  userPoolClientId: string;
}

let cached: AppConfig | null = null;

export async function loadConfig(): Promise<AppConfig> {
  if (cached) return cached;
  const res = await fetch("/config.json", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`Failed to load runtime config: ${res.status}`);
  }
  cached = (await res.json()) as AppConfig;
  return cached;
}
