# Deployment

## Container (any host)
```bash
docker build -t ujuzify .
docker run -d --name ujuzify -p 8000:8000 --env-file .env \
  -v ujuzify_feedback:/app/data/feedback -v ujuzify_processed:/app/data/processed ujuzify
```
The image is multi-stage (Node builds the UI, Python serves it), runs as a non-root user, exposes `/health` for
`HEALTHCHECK`, and reads `PORT`/`WEB_CONCURRENCY`. Put it behind a TLS-terminating proxy or load balancer and set
`APP_ENV=production` (enables HSTS) and a strong `API_KEY`.

## AWS (suggested, cost-conscious)
1. Push the image to ECR: `aws ecr create-repository --repository-name ujuzify`, then `docker tag/push`.
2. Run on **App Runner** (port 8000, health check path `/health`) or ECS Fargate behind an ALB.
3. Store `API_KEY` (and optional LLM keys) in Secrets Manager and inject as environment variables.
4. Persist `data/feedback` on EFS, or replace the CSV feedback store with the `app.recommendation_feedback`
   table in `database/schema.sql` before running multiple replicas.
5. Run the pipeline as a scheduled ECS task or Airflow (`python -m src.pipeline --load-db`) against RDS
   PostgreSQL, then call `POST /admin/reload`.

## Pre-launch checklist
- [ ] `API_KEY` set; `.env` never committed
- [ ] HTTPS in front of the app; `APP_ENV=production`
- [ ] `CORS_ORIGINS` empty (same-origin UI) or an explicit allow-list
- [ ] Rate limiting at the gateway for multi-replica deployments
- [ ] Pipeline run once and demand data reloaded; resource links re-verified
- [ ] Source terms checked before redistributing raw job descriptions
- [ ] Backups for PostgreSQL and the feedback store
- [ ] Prometheus scraping `/metrics` with the API key header
