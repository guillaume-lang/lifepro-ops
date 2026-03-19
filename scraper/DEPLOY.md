# Lifepro Scraper — Deployment Guide
# Google Cloud Run + Cloud Scheduler

## Prerequisites
- Google Cloud project (same one as the existing delivery map tool)
- `gcloud` CLI authenticated
- Supabase project with service key
- Monday.com API token
- Teams webhook URL (from channel connector)

---

## Step 1 — Environment variables

Create a `.env` file locally (never commit this):

```
SCRAPER_MODE=delivery
MONDAY_API_TOKEN=your_monday_v2_token
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key
TEAMS_WEBHOOK_URL=https://lifeprofitness.webhook.office.com/webhookb2/...
```

Store secrets in Google Secret Manager (recommended for Cloud Run):

```bash
gcloud secrets create MONDAY_API_TOKEN --data-file=- <<< "your_token"
gcloud secrets create SUPABASE_URL --data-file=- <<< "https://..."
gcloud secrets create SUPABASE_SERVICE_KEY --data-file=- <<< "your_key"
gcloud secrets create TEAMS_WEBHOOK_URL --data-file=- <<< "https://..."
```

---

## Step 2 — Build and push Docker image

```bash
PROJECT_ID=$(gcloud config get-value project)
IMAGE="gcr.io/$PROJECT_ID/lifepro-scraper:latest"

gcloud builds submit --tag $IMAGE .
```

---

## Step 3 — Deploy two Cloud Run Jobs

### Job 1: Delivery scraper (daily, 6am EST)
```bash
gcloud run jobs create lifepro-delivery-scraper \
  --image $IMAGE \
  --region us-central1 \
  --task-timeout 7200 \
  --max-retries 1 \
  --parallelism 1 \
  --set-env-vars SCRAPER_MODE=delivery \
  --set-secrets MONDAY_API_TOKEN=MONDAY_API_TOKEN:latest \
  --set-secrets SUPABASE_URL=SUPABASE_URL:latest \
  --set-secrets SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest \
  --set-secrets TEAMS_WEBHOOK_URL=TEAMS_WEBHOOK_URL:latest \
  --memory 2Gi \
  --cpu 2
```

### Job 2: Review monitor (4x daily)
```bash
gcloud run jobs create lifepro-review-monitor \
  --image $IMAGE \
  --region us-central1 \
  --task-timeout 1800 \
  --max-retries 1 \
  --parallelism 1 \
  --set-env-vars SCRAPER_MODE=reviews \
  --set-secrets MONDAY_API_TOKEN=MONDAY_API_TOKEN:latest \
  --set-secrets SUPABASE_URL=SUPABASE_URL:latest \
  --set-secrets SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest \
  --set-secrets TEAMS_WEBHOOK_URL=TEAMS_WEBHOOK_URL:latest \
  --memory 1Gi \
  --cpu 1
```

---

## Step 4 — Cloud Scheduler triggers

Create a service account for scheduler:
```bash
gcloud iam service-accounts create scraper-scheduler \
  --display-name "Scraper Scheduler"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member "serviceAccount:scraper-scheduler@$PROJECT_ID.iam.gserviceaccount.com" \
  --role "roles/run.invoker"
```

### Delivery scraper: daily at 6am EST (11am UTC)
```bash
gcloud scheduler jobs create http lifepro-delivery-daily \
  --location us-central1 \
  --schedule "0 11 * * *" \
  --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/lifepro-delivery-scraper:run" \
  --http-method POST \
  --oauth-service-account-email scraper-scheduler@$PROJECT_ID.iam.gserviceaccount.com \
  --time-zone "UTC"
```

### Review monitor: 4x daily (6am, 11am, 4pm, 9pm EST)
```bash
for HOUR in 11 16 21 2; do
  gcloud scheduler jobs create http lifepro-review-monitor-${HOUR}h \
    --location us-central1 \
    --schedule "0 $HOUR * * *" \
    --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/lifepro-review-monitor:run" \
    --http-method POST \
    --oauth-service-account-email scraper-scheduler@$PROJECT_ID.iam.gserviceaccount.com \
    --time-zone "UTC"
done
```

---

## Step 5 — Supabase schema

Run `supabase_schema.sql` in your Supabase SQL editor.
This creates:
- `delivery_results` table
- `review_snapshots` table
- `competitor_delivery_comparison` view
- `delivery_health_summary` view

---

## Step 6 — Test locally before deploying

```bash
# Test delivery scraper (small run)
docker build -t lifepro-scraper .
docker run --env-file .env -e SCRAPER_MODE=delivery lifepro-scraper

# Test review monitor
docker run --env-file .env -e SCRAPER_MODE=reviews lifepro-scraper
```

---

## Cost estimate

| Resource | Usage | Est. cost/month |
|---|---|---|
| Cloud Run Jobs compute | ~2h/day delivery + 4×30min reviews | ~$8–15 |
| Cloud Scheduler | 5 jobs | Free tier |
| Cloud Build | ~10 builds/month | Free tier |
| Supabase storage | ~500MB/month data | Free tier |
| **Total** | | **~$8–15/month** |

---

## Monitoring

View job logs:
```bash
gcloud run jobs executions list --job lifepro-delivery-scraper --region us-central1
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=lifepro-delivery-scraper" --limit 50
```

Check block rate in Supabase:
```sql
SELECT
  DATE(scraped_at) as date,
  COUNT(*) as total,
  SUM(CASE WHEN block_detected THEN 1 ELSE 0 END) as blocked,
  ROUND(100.0 * SUM(CASE WHEN block_detected THEN 1 ELSE 0 END) / COUNT(*), 1) as block_pct
FROM delivery_results
GROUP BY DATE(scraped_at)
ORDER BY date DESC;
```

---

## Proxy setup (if block rate > 10%)

Add to `delivery_scraper.py` browser launch args:
```python
browser = await p.chromium.launch(
    headless=True,
    proxy={
        "server": "http://proxy.webshare.io:80",
        "username": os.environ["PROXY_USER"],
        "password": os.environ["PROXY_PASS"],
    },
    args=["--no-sandbox"]
)
```

Webshare residential proxies: ~$20/month for 1GB.
Only needed for competitor scrape. Own ASIN scrape can stay direct.
