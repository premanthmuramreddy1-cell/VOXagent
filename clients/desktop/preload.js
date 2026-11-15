// Bridges the global shortcut into the page as a DOM event the web app already listens for.
const { ipcRenderer } = require('electron')
ipcRenderer.on('vox:talk', () => window.dispatchEvent(new Event('vox:talk')))
