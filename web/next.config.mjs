/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // /api/* is proxied by a runtime Route Handler (app/api/[...path]/route.ts),
  // NOT a next.config rewrite — rewrites bake their target at build time, which
  // froze the wrong URL into the image. The handler reads ORCHESTRATOR_URL live.
};

export default nextConfig;
