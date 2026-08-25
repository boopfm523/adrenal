import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: "public-healthcurve",
  base: "/healthcurve/",
  publicDir: false,
  plugins: [react()],
  build: {
    outDir: "../dist-public-healthcurve",
    emptyOutDir: true,
  },
});
