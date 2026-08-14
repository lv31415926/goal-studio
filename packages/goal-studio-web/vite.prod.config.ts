/**
 * 生产构建配置 — SPA 应用模式 (PR-S1/C-1)
 *
 * 与 vite.config.ts(lib 库模式) 分离: 库模式产出可复用 ESM bundle,
 * 本配置产出可部署的 SPA 应用(dist-app/),由后端 FastAPI 静态服务托管。
 *
 * root 指向 prod/ 目录,使 index.html 直接落在 dist-app/ 根(而非嵌套 prod/)。
 * 注意: react/react-dom 不打独立 chunk —— peer 依赖在 app 模式下随入口合并,
 * 强制分组会产出 28B 空壳 chunk。仅对真正的大依赖(@xyflow/react, jotai/zustand)分包。
 */
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  // prod/ 为 SPA 应用根目录(含 index.html)
  root: resolve(__dirname, "prod"),
  base: "/",
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  build: {
    outDir: resolve(__dirname, "dist-app"),
    emptyOutDir: true,
    // 生产关闭 sourcemap,避免暴露源码路径
    sourcemap: false,
    // 浏览器兼容目标
    target: "es2020",
    rollupOptions: {
      input: resolve(__dirname, "prod", "index.html"),
      output: {
        // 代码分割: 大体积第三方库独立 chunk,提升缓存命中与首屏加载
        manualChunks: {
          flow: ["@xyflow/react"],
          state: ["jotai", "zustand"],
        },
      },
    },
  },
});
