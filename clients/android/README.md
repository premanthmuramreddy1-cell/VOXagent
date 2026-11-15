# VoxAgent for Android

A Trusted Web Activity: the installed PWA in a Play Store package. It talks to the same API,
gets the same push notifications, and uses the phone's microphone through the web app.

1. Deploy the web app over HTTPS and set `host`, `iconUrl` and `webManifestUrl` in `twa-manifest.json`.
2. Build:
   ```bash
   npm install -g @bubblewrap/cli
   cd clients/android
   bubblewrap build          # creates app-release-signed.apk and .aab, and android.keystore on first run
   ```
3. Publish the SHA-256 fingerprint bubblewrap prints at `https://<host>/.well-known/assetlinks.json`
   so Android opens the app full-screen without a browser bar.

Without the Play Store, Chrome on Android can install the PWA directly: open the site → menu → Install app.
