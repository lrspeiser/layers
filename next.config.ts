import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  typescript: {
    tsconfigPath: process.env.VERCEL ? "tsconfig.vercel.json" : "tsconfig.json",
  },
};

export default nextConfig;
