# Telegram news formatter bot for Render

Webhook version of the Telegram bot for Render free web services.

## What it does

- Accepts photos, videos, and mixed media groups.
- Rewrites the first caption line in bold and adds a period if needed.
- Removes lines containing `Game InQisitor` or `Game InQsitor`.
- Replaces list marker emojis with `- `.
- Replaces the original final line with the configured final HTML line.
- Preserves Telegram formatting from captions by using `message.html_caption`.
- Adds inline buttons that send the edited post to the configured channels.

## Deploy on Render

1. Create a new GitHub repository with these files.
2. In Render, create a new **Web Service** from that repository.
3. Use the free plan.
4. Add environment variables:

   - `BOT_TOKEN`: token from BotFather.
   - `WEBHOOK_URL`: your Render service URL, for example `https://your-app.onrender.com`.
   - `WEBHOOK_SECRET`: any long random string using only `A-Z`, `a-z`, `0-9`, `_`, and `-`.
   - `FINAL_LINE`: optional final HTML line.
   - `MEDIA_TIMEOUT`: optional media group wait time, default `0.9`.
   - `CHANNEL_ID`: target channel ID for the inline send button.
   - `GP_CHANNEL_ID`: target GP channel ID for the second inline send button.

5. Deploy. On startup, the app registers the Telegram webhook automatically.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="1234567890:replace_me"
export WEBHOOK_URL="https://your-public-url.example"
export WEBHOOK_SECRET="replace_with_a_long_random_string"
export CHANNEL_ID="-1003371396924"
export GP_CHANNEL_ID="-1001262467981"
python app.py
```

For local testing with Telegram webhooks, `WEBHOOK_URL` must be a public HTTPS URL.

## Important

The bot token should never be committed to code. If you pasted a real token into chat or a public place, revoke it in BotFather and create a new one.

The bot must be an admin in the target channels and needs permission to post messages.
