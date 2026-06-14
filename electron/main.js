const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");
const fs = require("fs");

const ROOT = path.join(__dirname, "..");
const PORT = 8000;
let backend = null;

function pythonExe() {
  // dev: use the project venv; packaged: a bundled binary could be used instead
  const venvPy = process.platform === "win32"
    ? path.join(ROOT, "venv", "Scripts", "python.exe")
    : path.join(ROOT, "venv", "bin", "python");
  return fs.existsSync(venvPy) ? venvPy : "python3";
}

function healthy(cb) {
  const req = http.get(`http://127.0.0.1:${PORT}/api/health`, (res) => {
    res.resume();
    cb(res.statusCode === 200);
  });
  req.on("error", () => cb(false));
  req.setTimeout(800, () => { req.destroy(); cb(false); });
}

function startBackend() {
  if (app.isPackaged) {
    // bundled standalone backend (PyInstaller) shipped as an extra resource
    const dir = path.join(process.resourcesPath, "backend");
    const exe = process.platform === "win32"
      ? path.join(dir, "backend.exe")
      : path.join(dir, "backend");
    backend = spawn(exe, [], { cwd: dir, windowsHide: true });
  } else {
    // dev: run server.py with the project venv
    backend = spawn(pythonExe(), [path.join(ROOT, "server.py")], { cwd: ROOT, windowsHide: true });
  }
  backend.stdout.on("data", (d) => console.log("[backend]", d.toString().trim()));
  backend.stderr.on("data", (d) => console.log("[backend]", d.toString().trim()));
}

function waitForBackend(cb, tries = 40) {
  healthy((up) => {
    if (up) return cb();
    if (tries <= 0) return cb();
    setTimeout(() => waitForBackend(cb, tries - 1), 400);
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1140,
    height: 760,
    minWidth: 920,
    minHeight: 560,
    backgroundColor: "#0d1117",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    webPreferences: { contextIsolation: true },
  });
  win.loadFile(path.join(__dirname, "renderer", "index.html"));
}

app.whenReady().then(() => {
  // reuse an already-running backend, otherwise start our own
  healthy((up) => {
    if (!up) startBackend();
    waitForBackend(createWindow);
  });
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (backend) try { backend.kill(); } catch (e) {}
  app.quit();
});
