# Setting up the bot on AWS from scratch (beginner guide)

This takes about 20 minutes. You only need a web browser; everything happens in the AWS website and a terminal inside it.

> **Before you start**
> - **Your country must be allowed by Polymarket.** Check the [geographic restrictions](https://docs.polymarket.com/developers/CLOB/geoblock). The server location is for speed, not for getting around a restriction that applies to you.
> - **Use AWS region `eu-west-1` (Ireland / Dublin).** Do not use London (`eu-west-2`): Polymarket rejects new orders from UK IP addresses. Dublin is 1–2 ms from Polymarket's matching engine in London.
> - Start in **paper mode**. No wallet, keys or money are needed for steps 1–6.

---

## FASTEST WAY: one command in AWS CloudShell (about 2 minutes)

1. Log in at https://console.aws.amazon.com. At the **top right**, set the region to **Europe (Ireland) eu-west-1**.
2. Click the **CloudShell** icon (a small `>_` square) in the top bar. A terminal opens at the bottom of the page.
3. Paste this, replacing `YOUR_EMAIL` with your email, and press Enter:
   ```bash
   curl -sL https://raw.githubusercontent.com/xin10ylop/polynew/claude/quirky-rubin-rxd14g/scripts/aws_create_polybot.py | python3 - --email YOUR_EMAIL
   ```
   It creates the following, and is safe to run twice:
   - a server named `polybot` in Dublin, using a free-tier type if your account has one;
   - browser-only SSH access;
   - a $15/month budget alarm emailed to you;
   - the bot, installed and **running in paper mode automatically** (no keys, no money).
4. Open the **Connect** link it prints, choose the **EC2 Instance Connect** tab, and click **Connect**. Wait 3–5 minutes after creation, then run:
   ```bash
   cat ~/latency_probe.txt            # speed test: REST p50 should be <= ~20 ms
   tail -f ~/polynew/logs/paper.log   # watch the paper bot (Ctrl+C to stop watching)
   ```
   To see the paper profit and loss so far, compared with the backtest:
   ```bash
   cd ~/polynew && git pull -q && .venv/bin/python -m bot.summary
   ```
5. Then skip to **Step 7** below. Paper mode is already running as a service, so you don't need tmux.
   - Check it with `sudo systemctl status polybot-paper`.
   - Stop it with `sudo systemctl stop polybot-paper`.

---

The manual, step-by-step route follows if you prefer clicking through it yourself.

## Step 1: Pick the region

1. Log in at https://console.aws.amazon.com
2. At the **top right** there is a region name (for example "N. Virginia"). Click it and choose **Europe (Ireland) eu-west-1**.

## Step 2: Set a spending alarm (1 minute, strongly recommended)

1. In the top search bar, type **Budgets** and open it.
2. Click **Create budget**, then **Use a template**, then **Monthly cost budget**.
3. Enter an amount (for example **$40**) and your email, then click **Create budget**. AWS will email you if you get close.

## Step 3: Create the server (an "EC2 instance")

1. In the search bar, type **EC2** and open it. Click the orange **Launch instance** button.
2. Fill in:
   - **Name:** `polybot`
   - **Application and OS Images:** click **Ubuntu**, and keep "Ubuntu Server 24.04 LTS" (64-bit x86).
   - **Instance type:** `c7i.large`.
     - About $0.09/hour, or roughly $65/month if left running 24/7.
     - It has steady, fast CPUs, and low latency matters here.
     - For a cheap first test, `t3.small` (~$0.02/hour) works, but use `c7i.large` for real trading.
   - **Key pair:** click **Create new key pair**, name it `polybot-key`, keep the defaults, and click **Create key pair**. A `.pem` file downloads; keep it safe. It's a backup way to log in.
   - **Network settings:** leave **Allow SSH traffic from: Anywhere** ticked. The browser login below needs it, and Ubuntu only accepts key-based logins.
   - **Configure storage:** change 8 to **20** GiB.
3. Click **Launch instance**, then **View all instances**. Wait until "Instance state" shows **Running** (about 1 minute).

## Step 4: Open a terminal on the server (in your browser)

1. Tick the box next to `polybot` and click **Connect** at the top.
2. Choose the **EC2 Instance Connect** tab and click **Connect**.
3. A black terminal window opens in your browser. Everything below is typed or pasted there (right-click, then Paste, or Ctrl+Shift+V).

## Step 5: Install the bot (copy and paste this whole block)

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git tmux
git clone https://github.com/xin10ylop/polynew.git
cd polynew && git checkout claude/quirky-rubin-rxd14g
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt py-clob-client
```

**If `git clone` asks for a username or password**, your GitHub repo is private. Do this instead:

1. On github.com, go to your picture, then **Settings**, then **Developer settings**, then **Personal access tokens**, then **Fine-grained tokens**, then **Generate new token**.
2. Set **Repository access** to "Only select repositories" and choose `polynew`. Under **Permissions**, set **Contents** to "Read-only". Generate the token and copy it.
3. Run the following, replacing `YOUR_TOKEN`, then run the remaining lines of the block above:
   ```bash
   git clone https://YOUR_TOKEN@github.com/xin10ylop/polynew.git
   ```

## Step 6: Measure the speed (this decides everything)

```bash
cd ~/polynew && source .venv/bin/activate
python -m bot.latency_probe
```

Look at these lines:

- `REST /time round trip ms: p50` should be **≤ ~20 ms**.
- `feed ... jitter_p90_minus_min` should be **≤ ~30 ms**.

The backtests say the strategy needs **≤ ~50 ms** from a book change to the order reaching the exchange. If your numbers are far above that, **stop here**: the strategy loses money at 100 ms or more. Run it 2–3 times at different times of day.

## Step 7: Run paper mode for a few days (no money)

```bash
tmux new -s bot
cd ~/polynew && source .venv/bin/activate
BOT_MODE=paper BOT_PAPER_LAT_MS=20 python -m bot.run
```

- Press **Ctrl+b**, then **d**, to leave it running in the background. You can close the browser tab.
- To come back later: connect again (step 4), then run `tmux attach -t bot`.
- Every settled window prints a line like:
  `[settle] btc-updown-5m-... shadow ... pnl=+0.41 | gate_at_open=True | totals shadow=... gated=...`
- Full logs are in `~/polynew/logs/bot/`.
- Let it run **at least 2–3 days** and compare the `shadow` / `gated` totals with REPORT.md (about +$0.45 per window at 10-share clips).

## Step 8: Live trading (only after steps 6 and 7 look good)

1. Use a **dedicated Polymarket account with a small balance** (for example $100–200 USDC).
2. You need three values from your Polymarket account:
   - **Private key:** Polymarket website, then **Settings**, then **Export private key**. This is for accounts created with email login. For MetaMask, it's your wallet's private key.
   - **Funder / proxy address:** the deposit address shown in your Polymarket profile/wallet.
   - **Signature type:** `1` if you log in with email, `2` if you log in with a browser wallet like MetaMask.
3. On the server, create the secrets file. **Never paste these into chat or anywhere else.**
   ```bash
   cd ~/polynew
   nano .env
   ```
   Type these three lines, then save with **Ctrl+O**, **Enter**, **Ctrl+X**:
   ```
   POLY_PRIVATE_KEY=0x...
   POLY_FUNDER=0x...
   POLY_SIG_TYPE=1
   ```
   Then run:
   ```bash
   chmod 600 .env
   set -a && source .env && set +a
   ```
4. **Optional:** measure real order speed. This places tiny orders at 1¢, far below the market, and cancels them immediately.
   ```bash
   PROBE_ORDERS=1 python -m bot.latency_probe
   ```
   Check that `post_ms` is ≤ ~30 ms.
5. Start live with **tiny size** inside tmux:
   ```bash
   BOT_MODE=live BOT_SIZE=5 BOT_MAX_IMB=15 BOT_DAILY_LOSS=20 python -m bot.run
   ```
   - To stop everything immediately: in a second terminal, `cd ~/polynew && touch KILL`. Remove the file with `rm KILL` to allow quoting again.

## Step 9: Saving money when you're not using it

- **EC2**, then select `polybot`, then **Instance state**, then **Stop instance**. You then pay only ~$2/month for the disk. **Start instance** brings it back (the public IP may change; that's fine).
- **Terminate** deletes it completely.

## Troubleshooting

| problem | fix |
|---|---|
| "Connect" button fails | Make sure the instance is Running and its security group allows SSH (port 22) from Anywhere. |
| `ModuleNotFoundError` | You forgot `source .venv/bin/activate`. |
| Bot prints `lag_ms` in the hundreds or thousands | The server is too slow or overloaded. Don't trade; try a bigger instance or check the region is eu-west-1. |
| Orders rejected with a region/geoblock error | The server IP's country is restricted by Polymarket. Use eu-west-1 (Ireland), not London. |
