import os
import httpx
from datetime import datetime, timezone

TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")

SEVERITY_COLORS = {
    "critical": "FF0000",
    "warning":  "FFA500",
    "info":     "0078D4",
}


async def send_teams_alert(title: str, message: str, severity: str = "info"):
    """Send adaptive card to Teams channel via webhook."""
    if not TEAMS_WEBHOOK_URL:
        print(f"[alert] TEAMS_WEBHOOK_URL not set — skipping: {title}")
        return

    color = SEVERITY_COLORS.get(severity, "0078D4")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {
                        "type": "TextBlock",
                        "text": title,
                        "weight": "Bolder",
                        "size": "Medium",
                        "color": "Attention" if severity == "critical" else "Warning" if severity == "warning" else "Default",
                    },
                    {
                        "type": "TextBlock",
                        "text": message,
                        "wrap": True,
                        "size": "Small",
                    },
                    {
                        "type": "TextBlock",
                        "text": timestamp,
                        "size": "Small",
                        "isSubtle": True,
                    }
                ]
            }
        }]
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(TEAMS_WEBHOOK_URL, json=payload)
            if resp.status_code != 202:
                print(f"[alert] Teams webhook returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[alert] Failed to send Teams alert: {e}")
