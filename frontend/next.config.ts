import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // A second instance (tests, a production build) must not write into the dev server's .next.
  // Use: NEXT_DIST_DIR=.next-verify npm run build
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  /* config options here */
};

export default nextConfig;
