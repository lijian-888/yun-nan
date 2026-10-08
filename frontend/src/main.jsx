import React, { Suspense, lazy, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { initializeAuthentication, keycloak } from "./auth";
import { request } from "./api";
import "./styles.css";

const root = createRoot(document.getElementById("root"));
const DataGovernanceApp = lazy(() => import("./App"));
const ResearchAssistant = lazy(() => import("./ResearchAssistant"));

function AppRouter() {
  const [platformContext, setPlatformContext] = useState(null);
  const [contextError, setContextError] = useState("");
  const roles = keycloak.realmAccess?.roles || [];
  const user = {
    username: keycloak.tokenParsed?.preferred_username || "",
    display_name: [keycloak.tokenParsed?.family_name, keycloak.tokenParsed?.given_name].filter(Boolean).join(" ") || keycloak.tokenParsed?.preferred_username || "",
  };
  useEffect(() => {
    (async () => {
      try {
        setPlatformContext(await request("/api/context"));
      } catch (error) {
        setContextError(error.message || "无法进入云南省农业科学院工作环境。");
      }
    })();
  }, []);

  if (contextError) return <main className="auth-error">{contextError}</main>;
  if (!platformContext) return <main className="auth-error">正在进入云南省农业科学院工作环境…</main>;

  let page = <main className="auth-error">当前账号未配置三类业务角色，请联系字段管理员。</main>;
  const workspaceKey = roles.join(",");
  if (roles.includes("field_admin")) page = <DataGovernanceApp key={workspaceKey} user={user} accessRole="field_admin" platformContext={platformContext} />;
  else if (roles.includes("data_processor")) page = <DataGovernanceApp key={workspaceKey} user={user} accessRole="data_processor" platformContext={platformContext} />;
  else if (roles.includes("researcher")) page = <ResearchAssistant key={workspaceKey} platformContext={platformContext} />;
  return <Suspense fallback={<main className="auth-error">正在加载工作台…</main>}>{page}</Suspense>;
}

initializeAuthentication()
  .then(() => root.render(<AppRouter />))
  .catch(() => root.render(<main className="auth-error">身份认证服务不可用，请检查 Keycloak 是否已启动。</main>));
