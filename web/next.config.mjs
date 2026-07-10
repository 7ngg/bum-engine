/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // Proxy /api/* to the orchestrator at request time (server-side, runtime env).
  // Browser always calls same-origin /api, so no build-time API URL is baked in.
  // Local dev default: http://localhost:5080. In compose set ORCHESTRATOR_URL=http://api:8080.
  async rewrites() {
    const target = process.env.ORCHESTRATOR_URL || "http://localhost:5080";
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
};

export default nextConfig;
