import { spawn, spawnSync } from "node:child_process";
import path from "node:path";

export default async function setup() {
  const python = process.platform === "win32" ? path.resolve("../../.venv-trading/Scripts/python.exe") : "python";
  const child = spawn(python, ["-m", "streamlit", "run", "../../tests/trading/terminal_browser_app.py",
    "--server.address=127.0.0.1", "--server.port=8513", "--server.headless=true", "--browser.gatherUsageStats=false"], {
    windowsHide: true, stdio: "inherit",
    env: {...process.env, PYTHONUTF8:"1", PYTHONPATH:path.resolve("../../src"),
      NICE_WEATHER_TERMINAL_TEST_ROOT:path.resolve(`../../tmp/terminal-browser-${process.pid}`)},
  });
  const stop = () => {
    if(child.exitCode!==null)return;
    if(process.platform==="win32")spawnSync("taskkill",["/PID",String(child.pid),"/T","/F"],{windowsHide:true,stdio:"ignore"});
    else child.kill("SIGTERM");
  };
  try {
    const deadline=Date.now()+30000;
    while(Date.now()<deadline){
      if(child.exitCode!==null)throw Error(`Native app exited: ${child.exitCode}`);
      try {if((await fetch("http://127.0.0.1:8513/_stcore/health")).ok)return stop;} catch {}
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    throw Error("Native browser app did not start");
  } catch(error){stop();throw error;}
}
