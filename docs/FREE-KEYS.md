# Getting every key for free

Every integration has a free route. Do them in this order: easiest first. Each one goes into `backend/.env`
(restart the API afterwards) unless it says otherwise. Never paste keys into chat or commit `.env`.

## 1. Push notifications — 1 minute, no account

```bash
cd backend
uv run python -m app.push keys
```

Copy the `VOX_VAPID_PRIVATE_KEY=…` line it prints into `backend/.env`. Then Notes → **Turn on notifications**.

## 2. Telegram bot — 2 minutes, free

1. In Telegram, open **@BotFather**, send `/newbot`, pick a name and a username ending in `bot`.
2. BotFather replies with a token like `123456:ABC…`. Add `VOX_TELEGRAM_TOKEN=<token>` to `.env`.
3. Restart the API, then Account → Telegram → **Get code**, and send `/link CODE` to your bot.

No public URL needed: the server polls Telegram.

## 3. GitHub — 2 minutes, free, no `.env`

1. github.com → Settings → Developer settings → **Fine-grained tokens** → Generate.
2. Repository access: the repos you want. Permissions: **Contents** and **Pull requests** → Read and write.
3. In VoxAgent: Account → GitHub → **Use a token**, paste it. It is stored encrypted.

## 4. Google sign-in, Gmail and Calendar — 10 minutes, free

1. Go to console.cloud.google.com → create a project (free, no billing needed).
2. APIs & Services → Library → enable **Gmail API** and **Google Calendar API**.
3. OAuth consent screen → External → fill the app name and your email → under **Test users** add your own
   Google address. (Testing mode is free; refresh tokens last 7 days, then reconnect.)
4. Credentials → Create credentials → **OAuth client ID** → Web application:
   - Authorized JavaScript origins: `http://localhost:5173`
   - Authorized redirect URIs: `http://localhost:5173/integrations/google/callback`
5. Copy the client ID and secret into `.env`:
   ```
   VOX_GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   VOX_GOOGLE_CLIENT_SECRET=...
   ```
6. Restart. The login page shows "Continue with Google"; Account → Google → **Connect** enables Gmail and Calendar.

## 5. Stripe payments — 5 minutes, free test mode (no real money)

1. Sign up at dashboard.stripe.com and keep **Test mode** on.
2. Product catalog → add a product with a **recurring** price → copy the price ID (`price_…`).
3. Developers → API keys → copy the **secret key** (`sk_test_…`).
4. For the webhook on your laptop, install the Stripe CLI (`brew install stripe/stripe-cli/stripe`), then:
   ```bash
   stripe login
   stripe listen --forward-to localhost:8000/webhooks/stripe   # prints a whsec_… secret
   ```
5. In `.env`: `VOX_STRIPE_SECRET_KEY=sk_test_…`, `VOX_STRIPE_PRICE_ID=price_…`, `VOX_STRIPE_WEBHOOK_SECRET=whsec_…`.
6. Account → **Upgrade**, pay with Stripe's test card `4242 4242 4242 4242` (any future date, any CVC).

## 6. Free hosting and deploys — 10 minutes

- **Render** (free web services): connect your GitHub repo, create a Web Service, then Settings → Deploy Hook →
  copy the URL and set `VOX_DEPLOY_PROVIDER=render:<that URL>`.
- **Vercel** (free Hobby plan, good for static and Next.js apps): `npm i -g vercel`, create a token at
  vercel.com/account/tokens, set `VOX_DEPLOY_PROVIDER=vercel` and `VERCEL_TOKEN=<token>` in the environment.

## 7. WhatsApp — 15 minutes, free test number

WhatsApp needs a public HTTPS address for its webhook, so do this after Render, or use a tunnel.

1. developers.facebook.com → My Apps → Create app → type **Business** → add **WhatsApp**.
2. WhatsApp → API Setup gives a free **test number**, a **Phone number ID** and a temporary access token
   (24 hours; create a permanent System User token later). Add your own phone as a recipient.
3. App settings → Basic → **App secret**.
4. Pick any verify string yourself. In `.env`:
   ```
   VOX_WHATSAPP_TOKEN=...
   VOX_WHATSAPP_PHONE_ID=...
   VOX_WHATSAPP_APP_SECRET=...
   VOX_WHATSAPP_VERIFY_TOKEN=<your own string>
   ```
5. Expose the API: `cloudflared tunnel --url http://localhost:8000` prints a free `https://….trycloudflare.com`.
   In WhatsApp → Configuration → Webhook: callback `https://….trycloudflare.com/webhooks/whatsapp`, the same
   verify string, then subscribe to **messages**.
6. Account → WhatsApp → **Get code**, then message `link CODE` to the test number.

## 8. Android app — after hosting

The Android wrapper needs the web app on a public HTTPS address (Render's free URL works).
Then follow `clients/android/README.md`. Until then, Chrome on Android can install the PWA directly.

## The model

Already free: Ollama runs locally. Free cloud options with an OpenAI-compatible API: Groq, Z.ai, OpenRouter
free models, Google Gemini. Set `VOX_LLM=cloud:<model>,ollama:<model>`, `VOX_LLM_BASE_URL` and `VOX_LLM_API_KEY`.
