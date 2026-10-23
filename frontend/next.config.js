/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export so the cockpit can be hosted on S3 + CloudFront (no Node
  // server). Runtime config (API/AppSync/Cognito) is fetched from /config.json,
  // which the CDK stack writes into the hosting bucket at deploy time.
  output: "export",
  reactStrictMode: true,
  images: { unoptimized: true },
  trailingSlash: true,
};

module.exports = nextConfig;
