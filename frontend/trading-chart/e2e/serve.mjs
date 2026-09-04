import { spawn, spawnSync } from "node:child_process";
import path from "node:path";

async function waitUntilReady(child) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Streamlit exited with ${child.exitCode}`);
    try {
      const response = await fetch("http://127.0.0.1:8511/_stcore/health");
      if (response.ok) return;
    } catch {
      // The server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Timed out waiting for the Streamlit test server");
}

export default async function globalSetup() {
  const python = process.platform === "win32"
    ? path.resolve("../../.venv-codex/Scripts/python.exe")
    : "python";
  const child = spawn(python, ["e2e/serve_streamlit.py"], { stdio: "inherit" });
  child.unref();
  await waitUntilReady(child);
  return async () => {
    if (child.exitCode !== null || child.pid == null) return;
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      child.kill("SIGTERM");
    }
  };
}
