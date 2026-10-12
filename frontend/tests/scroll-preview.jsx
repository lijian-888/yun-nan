// Isolated UI fixture. Not the production entry point; never calls a business database.
import React from "react";
import { createRoot } from "react-dom/client";
import VarietySearchWorkspace from "../src/VarietySearchWorkspace";
import "../src/styles.css";

createRoot(document.getElementById("root")).render(<div className="research-shell"><aside className="research-sidebar">仅布局回归测试<br />不是真实业务资料</aside><main className="research-main structured-main"><header className="research-topbar"><div><p>滚动与弹窗关闭回归测试</p><h1>结构化查询</h1></div></header><VarietySearchWorkspace /></main></div>);
