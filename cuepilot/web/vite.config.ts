import { defineConfig, loadEnv, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// The proxy holds the credential. No define, VITE_ variable, or client import.
export default defineConfig(({ mode }) => {
  const token =
    process.env.CUEPILOT_OPERATOR_TOKEN ??
    loadEnv(mode, process.cwd(), "CUEPILOT_").CUEPILOT_OPERATOR_TOKEN;
  const sameOriginWrites: Plugin = {
    name: "cuepilot-local-write-origin",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (
          !req.url?.startsWith("/api") ||
          ["GET", "HEAD", "OPTIONS"].includes(req.method ?? "GET")
        )
          return next();
        const origin = req.headers.origin;
        if (origin && origin !== `http://${req.headers.host}`) {
          res.writeHead(403, { "Content-Type": "application/json" });
          res.end(
            JSON.stringify({
              error: "Operator writes must originate from this local desk.",
            }),
          );
          return;
        }
        next();
      });
    },
  };
  return {
    plugins: [sameOriginWrites, react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      cors: false,
      proxy: {
        "/api": {
          target: "http://127.0.0.1:8787",
          changeOrigin: true,
          configure(proxy) {
            proxy.on("proxyReq", (proxyReq, req) => {
              proxyReq.removeHeader("Authorization");
              if (
                !["GET", "HEAD", "OPTIONS"].includes(req.method ?? "GET") &&
                token
              ) {
                proxyReq.setHeader("Authorization", `Bearer ${token}`);
              }
            });
          },
        },
      },
    },
    preview: { host: "127.0.0.1", port: 4173, strictPort: true },
  };
});
