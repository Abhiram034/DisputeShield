import type { NextConfig } from "next";

const configuredApiBase = process.env.API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL;
if (process.env.VERCEL && !configuredApiBase) {
  throw new Error("Set API_BASE_URL to the deployed DisputeShield API URL in Vercel project settings.");
}
const apiBase = configuredApiBase || "http://localhost:8000";
const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/backend/:path*", destination: `${apiBase}/:path*` }];
  },
};

export default nextConfig;
