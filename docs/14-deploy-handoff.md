# Deploy handoff: the screen on Vercel, the backend on Fly.io

Written for whoever connects the domain, and for their Claude Code session. Everything here was
set up and checked on Sept 21; the only things not done are the two accounts and the domain,
which are yours. Read this whole page once, then do the steps in order. Each step ends with a check.

## What you are deploying

Two pieces. They are wired together by two environment variables and nothing else.

| Piece | Lives in | Runs on | Why not Vercel |
|---|---|---|---|
| The screen | `frontend/` | Vercel | It is a Next.js app; this is what Vercel is for |
| The backend | `backend/` | Fly.io, from `backend/Dockerfile` | One persistent process holding a simulation, a WebSocket hub, ffmpeg and a Whisper model. Vercel functions are stateless and time-limited |

The screen works with no backend at all: a hosted copy with nothing configured opens on the
scripted demo. So the order is backend first, then the screen, and if the backend is not ready
you can still ship the screen and add the two variables later.

## Before you start

```bash
# Fly
brew install flyctl && fly auth login
# Vercel
npm i -g vercel && vercel login
# the repo, on main
git clone https://github.com/draggle/squawk.git && cd squawk
```

Keys you will need, all optional but the demo is far better with the first three:

| Secret | What it turns on | Without it |
|---|---|---|
| `ELEVENLABS_API_KEY` | The pilot voices | The backend is silent (no macOS `say` in a container) |
| `OPENAI_API_KEY` | The squack chat agent | A keyword router for the simple phrases |
| `BASETEN_API_KEY`, `ASR_MODEL_URL` | The fine-tuned Whisper and the resolver and interpreter agents | Local stock Whisper, deterministic mock agents |
| `TOWER_TOKEN` | Locks the backend to your screen. **Set this.** | Anyone who finds the URL can drive the world and burn the keys above |

Make the token now and keep it for step 2: `openssl rand -hex 24`.

## Step 1: the backend on Fly.io

```bash
cd backend
fly launch --copy-config --no-deploy      # reads fly.toml; say yes to the app name it proposes, or pick one; no Postgres, no Redis
fly secrets set \
  TOWER_TOKEN=<the token> \
  ELEVENLABS_API_KEY=... \
  OPENAI_API_KEY=... \
  BASETEN_API_KEY=... ASR_MODEL_URL=... \
  TOWER_ALLOWED_ORIGINS=https://<your domain>,https://www.<your domain>
fly deploy                                 # builds the Dockerfile remotely, about 5 minutes the first time
```

Leave out any secret you do not have. `TOWER_ALLOWED_ORIGINS` can be set later once the domain is
known; unset, the backend answers any origin.

The image is about 2.8 GB, most of it PyTorch pulled in by the voice activity detector, so the first
remote build takes a while and the machine wants the 2 GB in `fly.toml`. Built and run here on Sept 21:
`/health` answered 2 s after start, the gate refused a socket with no token and opened one with it.

`fly.toml` already says: one machine, never auto-stopped (the world lives in the process), 2 GB,
health check on `/health`, HTTPS forced. If `fly launch` asks to overwrite settings from the file,
say no.

**Check.** Replace the hostname with what `fly status` prints.

```bash
curl https://squack-backend.fly.dev/health
# {"ok":true,"scenario":null,"t":0.0,"lifecycle":"idle",...}

# the token gate: this must fail, closed before accept
python3 -c "import websockets,asyncio
async def m():
    try:
        async with websockets.connect('wss://squack-backend.fly.dev/ws'): print('OPEN: token gate is NOT working')
    except Exception as e: print('closed as expected:', type(e).__name__)
asyncio.run(m())"
# and this must succeed
python3 -c "import websockets,asyncio
async def m():
    async with websockets.connect('wss://squack-backend.fly.dev/ws?token=<the token>') as ws: print('open:', (await ws.recv())[:60])
asyncio.run(m())"
```

If the health check fails for the first minute after deploy, that is the model loading; the grace
period in `fly.toml` is 90 s. `fly logs` shows `ASR ready: LocalWhisper` when it is up.

## Step 2: the screen on Vercel, by CLI

The three `NEXT_PUBLIC_` values are inlined at build time, so set them **before** the production
deploy, not after.

```bash
cd ../frontend
vercel link                                # create a new project when asked; root is this directory, so no Root Directory setting is needed for CLI deploys
vercel env add NEXT_PUBLIC_TOWER_WS production      # wss://squack-backend.fly.dev/ws
vercel env add NEXT_PUBLIC_TOWER_HTTP production    # https://squack-backend.fly.dev
vercel env add NEXT_PUBLIC_TOWER_TOKEN production   # the same token as TOWER_TOKEN
vercel --prod
```

If you would rather connect through the dashboard (Git integration, deploy on push), import
`draggle/squawk` and set **Root Directory** to `frontend`. Add the same three variables under
Settings, Environment Variables, then redeploy. Either way works; the CLI route is the one that
does not need the Root Directory setting.

**Check.** Open the deployment URL Vercel prints.

- The boot screen shows "squack." and the setup dialog opens. The top bar should say nothing about
  mock or reconnecting once a sky is loaded. If it says `mock`, the variables were not present at
  build time: run `vercel env ls production`, then `vercel --prod` again.
- Load a live region, press Start, and hold Space: the browser asks for the microphone. That
  needs HTTPS, which Vercel gives you.

## Step 3: the domain

```bash
vercel domains add <your domain>            # prints the DNS records to set
vercel domains inspect <your domain>        # until it says configured
```

Then set `TOWER_ALLOWED_ORIGINS` on Fly to the final domains if you skipped it in step 1:

```bash
cd ../backend && fly secrets set TOWER_ALLOWED_ORIGINS=https://<your domain>,https://www.<your domain>
```

## Step 4: end to end

On the domain: load the live Europe sky, press Start, drop a storm from the Disrupt button, and
watch a card appear. Hold ⌘⇧ and ask "which two flights are closest". If the answer comes back, the
screen, the backend, the token and the agent key are all wired.

## If something is off

| Symptom | Cause | Fix |
|---|---|---|
| Screen says `mock` | `NEXT_PUBLIC_` variables missing at build | `vercel env ls production`, add, `vercel --prod` |
| Screen says `reconnecting` forever | Wrong `NEXT_PUBLIC_TOWER_WS`, or token mismatch, or backend down | `curl .../health`; compare `TOWER_TOKEN` and `NEXT_PUBLIC_TOWER_TOKEN` exactly |
| Browser console: mixed content | `ws://` instead of `wss://` | The backend URL must be `wss://` and `https://` |
| First transmission takes 16 s | Baseten scale-to-zero after a quiet half hour | Known; the app keeps it warm while a screen is connected and voice is on |
| Pilots do not speak | No `ELEVENLABS_API_KEY` on Fly | `fly secrets set ELEVENLABS_API_KEY=...` |
| CORS error on `/audio` or `/scenarios` | `TOWER_ALLOWED_ORIGINS` set without the exact origin | Add the origin, scheme included, no trailing slash |

Rollback: `vercel rollback` for the screen, `fly releases` then `fly deploy --image <previous>` for
the backend. To take the backend down and leave the screen on the scripted demo, remove the three
`NEXT_PUBLIC_` variables and redeploy the screen; then `fly scale count 0`.

## What this deployment is, and is not

- **One world.** The backend holds a single simulation. Every visitor with the token sees and
  drives the same sky. That is right for a demo you present and wrong for a public playground.
  Per-visitor worlds are a backend change, not a setting.
- **The token is the whole lock.** It is inlined into the screen's JavaScript, so anyone who reads
  the page source has it. It stops drive-by visitors and crawlers, not a determined person. Rotate
  it (`fly secrets set` and `vercel env`, then redeploy both) if the site is ever shared widely.
- **Checking is rules only** unless `CHECKER_MODEL_URL` points at a served cross-encoder; none is
  deployed. The alerts still fire; the trained model is not in the loop.
- **Miles saved over the live sky is zero by construction**, because live routes are straight
  projections. Do not present that number there.

## Files this relies on

| File | What it does |
|---|---|
| `backend/Dockerfile` | python 3.11, ffmpeg, the dependencies, local Whisper `base.en` baked in, `uvicorn --host 0.0.0.0` |
| `backend/fly.toml` | One machine, 2 GB, no auto-stop, `/health` check, HTTPS |
| `backend/app.py` | `TOWER_TOKEN` gate on `/ws`, `TOWER_ALLOWED_ORIGINS` for CORS |
| `frontend/lib/ws.ts` | Sends `?token=` from `NEXT_PUBLIC_TOWER_TOKEN`; a hosted page with no `NEXT_PUBLIC_TOWER_WS` opens on the scripted demo |
| `frontend/vercel.json` | Names the framework |
| `.env.example` | Every variable, with what each one does |
