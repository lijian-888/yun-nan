// Test entry only. Uses an exported read-only public-data fixture, not production routing.
import React from "react";
import { createRoot } from "react-dom/client";
import PedigreeWorkspace from "../src/PedigreeWorkspace";
import "../src/styles.css";

createRoot(document.getElementById("root")).render(<div className="research-shell"><aside className="research-sidebar">仅图谱布局验证<br/>公共系谱实库快照</aside><main className="research-main pedigree-main"><PedigreeWorkspace /></main></div>);
