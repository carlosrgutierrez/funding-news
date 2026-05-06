# Google Cloud Setup

This guide sets up the bot on a Google Cloud VM.

Plain English: Google Cloud gives us a computer in the cloud. The bot runs on that computer. Playwright opens Discord there and posts the daily message.

## 1. Create The VM

In Google Cloud, create a Compute Engine VM.

Recommended starting setup:

- OS: Ubuntu 24.04 LTS
- Machine: small general-purpose VM
- Disk: 20 GB or more
- Network: allow outbound internet

The VM must stay running for the daily post to happen.

## 2. Install System Packages

SSH into the VM and run:

```bash
sudo apt update
sudo apt install -y curl git ca-certificates
```

## 3. Install Node.js

Install Node.js 20:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
node --version
npm --version
```

## 4. Clone The Repo

```bash
git clone https://github.com/drozrzd/startup-news.git
cd startup-news
```

After the repo is transferred to Carlos, use Carlos's repo URL instead.

## 5. Install The Bot

```bash
npm install
npx playwright install --with-deps chromium
```

## 6. Create The `.env` File

```bash
cp .env.example .env
nano .env
```

Use this while testing:

```env
DRY_RUN=true
HEADLESS=false
```

Use this after testing:

```env
DRY_RUN=false
HEADLESS=true
```

## 7. Log Into Discord Once

Run:

```bash
npm run login
```

Discord will open in the Playwright browser. Log in as the Discord account that can access the channel.

When the channel is visible, stop the command with `Ctrl+C`.

The login is saved in `playwright-profile/`.

## 8. Preview The Message

```bash
npm run preview
```

If there are no eligible pre-seed, seed, or Series A items in the last 48 hours, the bot will say so and skip posting.

## 9. Post Manually Once

Set this in `.env`:

```env
DRY_RUN=false
HEADLESS=true
```

Then run:

```bash
npm run post
```

## 10. Add The Daily Scheduler

Create a service:

```bash
sudo nano /etc/systemd/system/startup-news.service
```

Paste this, replacing `/home/YOUR_USER/startup-news` with the real folder:

```ini
[Unit]
Description=Startup News Discord Bot
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/YOUR_USER/startup-news
ExecStart=/usr/bin/npm run post
Environment=NODE_ENV=production
```

Create a timer:

```bash
sudo nano /etc/systemd/system/startup-news.timer
```

Paste:

```ini
[Unit]
Description=Run Startup News Discord Bot on weekdays

[Timer]
OnCalendar=Mon..Fri 05:00:00
Persistent=true
Unit=startup-news.service

[Install]
WantedBy=timers.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now startup-news.timer
systemctl list-timers startup-news.timer
```

## 11. Check Logs

```bash
journalctl -u startup-news.service -n 100 --no-pager
```

## If It Breaks

Most likely causes:

- Discord asks for login again.
- The VM is off.
- Discord changed its page layout.
- There were no good startup news items that day.

Fixes:

- If Discord asks for login, run `npm run login` again.
- If the VM is off, start it again in Google Cloud.
- If Discord changed layout, the Playwright selector needs a small code update.
- If there is no good news, the bot should skip instead of posting filler.
