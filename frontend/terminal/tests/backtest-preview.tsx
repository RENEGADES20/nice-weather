// Local Vite entry only; excluded from the production terminal build.
import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";
import { BacktestWorkspace } from "../src/backtest/BacktestWorkspace";

function Preview() {
  const [csrf, setCsrf] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => { fetch("/api/session").then(r => r.ok ? r.json() : null).then(r => r && setCsrf(r.csrf)); }, []);
  return <><p style={{ padding: "0 24px", fontFamily: "sans-serif" }}>本地独立组件验收入口 · 数据由所连接 API 提供</p>
    {csrf != null ? <BacktestWorkspace csrf={csrf} /> : <form onSubmit={async e => {
      e.preventDefault(); const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }) });
      if (r.ok) { setCsrf((await r.json()).csrf); setPassword(""); } else setMessage("登录失败");
    }}><label>本地测试密码<input type="password" value={password} onChange={e => setPassword(e.target.value)} /></label><button>登录</button><p>{message}</p></form>}
  </>;
}
createRoot(document.getElementById("root")!).render(<Preview />);
