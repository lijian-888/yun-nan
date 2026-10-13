// node node_modules/vite/bin/vite.js --config tests/pedigree-preview.config.js
// Requires PEDIGREE_PREVIEW_FIXTURE: JSON exported by a read-only server probe.
// These endpoints exist ONLY in this loopback test server, never the production build.
import { readFileSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

if(!process.env.PEDIGREE_PREVIEW_FIXTURE)throw new Error("Provide a real public pedigree JSON fixture; no sample fallback.");
const fixture=JSON.parse(readFileSync(process.env.PEDIGREE_PREVIEW_FIXTURE,"utf8"));
export default defineConfig({plugins:[react(),{name:"public-pedigree-layout-test",configureServer(server){
  server.middlewares.use("/api/research",(req,res)=>{
    const url=new URL(req.url,"http://localhost");
    let answer;
    if(url.pathname==="/pedigree/catalog")answer=fixture.catalog;
    if(url.pathname==="/pedigree/varieties"){
      let records=Object.values(fixture.graphs).map(g=>({...g.variety,node_count:g.node_count,edge_count:g.edge_count,max_depth:g.snapshot.max_depth}));
      const q=(url.searchParams.get("q")||"").toLowerCase();records=records.filter(r=>r.name.toLowerCase().includes(q));
      answer={records,total:records.length,page:1,page_size:30,has_more:false,keyword:q,group:url.searchParams.get("group")||""};
    }
    const graph=url.pathname.match(/^\/pedigree\/varieties\/(\d+)\/graph$/);
    if(graph)answer=fixture.graphs[graph[1]];
    const detail=url.pathname.match(/^\/variety-search\/ricedata\/(\d+)$/);
    if(detail)answer=fixture.details[detail[1]];
    res.setHeader("Content-Type","application/json");
    if(!answer){res.statusCode=404;answer={detail:"该公开实库快照未包含此记录，仅用于布局测试。"};}
    res.end(JSON.stringify(answer));
  });
}}],server:{host:"127.0.0.1",port:5198,strictPort:true}});
