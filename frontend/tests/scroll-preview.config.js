// Run only with: npx vite --config tests/scroll-preview.config.js
// Mock endpoints exist ONLY in this loopback development server, never production.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react(), {
    name: "isolated-scroll-layout-fixtures",
    configureServer(server) {
      server.middlewares.use("/api/research/variety-search", (req, res) => {
        const records = Array.from({ length: 20 }, (_, index) => ({ id: index + 1, name: `布局测试品种${index + 1}`, aliases: [], variety_type: "仅测试布局", parentage: "不是真实资料", breeder: "仅测试布局", regions: ["云南"], latest_year: 2020, approval_count: 1 }));
        const answer = req.url.startsWith("/catalog") ? { sources: [{ code: "ricedata", name: "布局测试来源", count: 100 }], regions: [{ name: "云南", count: 100 }], approval_years: [2020], traits: [] }
          : req.url.startsWith("/query") ? { source: "ricedata", total: 100, page: 1, page_size: 20, has_more: true, records, query: { source: "ricedata", keyword: "", traits: [], page: 1, page_size: 20 } }
          : { source: "ricedata", record: records[0], evidence: [], content: Array.from({ length: 50 }, (_, index) => `### 测试段落 ${index + 1}\n\n仅布局回归测试内容，不是真实业务资料。用于验证正文滚动到底部后，顶部与底部关闭按钮仍然可见。`).join("\n\n") };
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify(answer));
      });
    },
  }],
  server: { host: "127.0.0.1", port: 5197, strictPort: true },
});
