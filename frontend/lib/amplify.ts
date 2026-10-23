// Amplify configuration for Cognito auth + AppSync GraphQL (subscriptions).
//
// Configured at runtime from the fetched AppConfig so the same static bundle
// works against any deployed stack.

import { Amplify } from "aws-amplify";
import type { AppConfig } from "./config";

let configured = false;

export function configureAmplify(config: AppConfig): void {
  if (configured) return;
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: config.userPoolId,
        userPoolClientId: config.userPoolClientId,
      },
    },
    API: {
      GraphQL: {
        endpoint: config.realtimeApiUrl,
        region: config.region,
        defaultAuthMode: "userPool",
      },
    },
  });
  configured = true;
}
