import type { NextConfig } from "next";

// The browser talks only to this origin. /api and /mock-portal are proxied to
// the FastAPI backend, so the session cookie is first-party and no secret or
// backend URL is ever shipped in the client bundle.
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Agent runs and the Test Lab can take longer than the default 30 s proxy limit.
  experimental: { proxyTimeout: 120_000 },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/mock-portal/:path*", destination: `${backend}/mock-portal/:path*` },
    ];
  },
};

export default nextConfig;
