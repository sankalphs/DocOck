import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The dashboard is a pure client of the API: no Docker, no keys, no server-side
  // data fetching. That keeps `next build` hermetic (it must not need the stack).
  poweredByHeader: false,
};

export default nextConfig;
