import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep Next's development badge out of the sidebar account/avatar area.
  devIndicators: { position: "bottom-right" },
};

export default nextConfig;
