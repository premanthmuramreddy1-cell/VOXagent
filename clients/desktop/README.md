# VoxAgent desktop

The same web app in its own window, plus a system-wide push-to-talk shortcut
(`Cmd/Ctrl+Shift+Space`, change with `VOXAGENT_SHORTCUT`). No logic lives here.

```bash
cd clients/desktop
npm install
VOXAGENT_URL=https://your-voxagent-host npm start   # defaults to http://localhost:5173
npm run dist                                         # installers for macOS, Windows, Linux
```
