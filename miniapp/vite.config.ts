import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/miniapp/",
  server: {
    port: 5173,
    allowedHosts: ["1768-85-190-233-230.ngrok-free.app"],
    proxy: {
      "/v1": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
