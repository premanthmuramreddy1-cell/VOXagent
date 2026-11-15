// VoxAgent desktop: a thin client. It loads the web app (all logic stays on the server) and adds a
// system-wide push-to-talk shortcut so you can talk to the agent from any app.
const { app, BrowserWindow, globalShortcut, session, shell } = require('electron')
const path = require('node:path')

const APP_URL = process.env.VOXAGENT_URL || 'http://localhost:5173'
const SHORTCUT = process.env.VOXAGENT_SHORTCUT || 'CommandOrControl+Shift+Space'
let win

function createWindow() {
  win = new BrowserWindow({
    width: 1200, height: 820, title: 'VoxAgent', backgroundColor: '#0a0c10',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true },
  })
  // Only the VoxAgent origin may use the microphone; everything else opens in the real browser.
  const origin = new URL(APP_URL).origin
  session.defaultSession.setPermissionRequestHandler((wc, permission, done) =>
    done(permission === 'media' && new URL(wc.getURL()).origin === origin))
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })
  win.loadURL(`${APP_URL}/#/`)
}

app.whenReady().then(() => {
  createWindow()
  globalShortcut.register(SHORTCUT, () => {
    if (!win) return
    win.show()
    win.webContents.send('vox:talk')
  })
})
app.on('will-quit', () => globalShortcut.unregisterAll())
app.on('window-all-closed', () => process.platform !== 'darwin' && app.quit())
app.on('activate', () => BrowserWindow.getAllWindows().length === 0 && createWindow())
