import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

const sourceDirectory = resolve(process.cwd(), "src");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": resolve(sourceDirectory),
    },
  },
  test: {
    environment: "node",
  },
});
