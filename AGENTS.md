# Repository Guidelines

## Project Structure & Module Organization
- `docker-compose.yml`: Standalone deployment with Traefik, Redis, Postgres, n8n, workers, autoscaler, and monitor.
- `docker-compose.dockploy.yml`: Dockploy/Docker Swarm deployment variant.
- `Dockerfile`: Base n8n image (Node 20, Chromium, Puppeteer).
- `autoscaler/`: Python autoscaler (`autoscaler.py`) + image `autoscaler/Dockerfile`.
- `monitor/`: Redis queue monitor (`monitor_redis_queue.py`) + image `monitor.Dockerfile`.
- `init-pgvector.sql/init.sql`: Enables the `vector` extension on Postgres init.
- `.env.example`: Copy to `.env` and set strong secrets.

## Build, Test, and Development Commands
- Standalone (Compose):
  - `docker network create shark` (one-time external network)
  - `docker compose up -d` (builds and starts all services)
  - `docker compose logs -f n8n-autoscaler` (watch scaling decisions)
  - Update: `docker compose down && docker compose build --no-cache && docker compose up -d`
- Dockploy/Swarm (optional):
  - Build images: `docker build -t n8n-autoscaling:latest -f Dockerfile .` (and autoscaler/monitor images similarly)
  - Deploy: `docker stack deploy -c docker-compose.dockploy.yml n8n-autoscaling`

## Coding Style & Naming Conventions
- Python: PEP 8, 4-space indent, `snake_case` for modules/functions; prefer type hints and concise logging via `logging`.
- YAML/Compose: 2-space indent; service names `kebab-case` (e.g., `n8n-worker`); UPPER_SNAKE_CASE env vars.
- Do not rename services without updating autoscaler envs (`N8N_WORKER_SERVICE_NAME`, `COMPOSE_PROJECT_NAME`).

## Testing Guidelines
- No unit tests yet; validate via integration runs.
- Check queue length: `docker compose exec redis redis-cli LLEN bull:jobs:wait`.
- Observe autoscaler actions: `docker compose logs -f n8n-autoscaler`.
- For Swarm, verify with `docker service ls` and `docker service scale ...` (when testing manually).

## Commit & Pull Request Guidelines
- Use short, imperative subjects (e.g., “Add Dockploy support”, “Fix autoscaler replicas”). Optional scope: `autoscaler:`, `compose:`.
- Include: what changed, why, how tested (commands/logs), and any env/compose updates.
- Keep PRs focused; update `README.md`/`.env.example` when modifying config or behavior.

## Security & Configuration Tips
- Never commit `.env`. Start from `.env.example` and set strong `N8N_ENCRYPTION_KEY`, DB passwords, tokens.
- Prefer binding Postgres via `TAILSCALE_IP` if remote access is required.
- Autoscaler requires `/var/run/docker.sock`; ensure it remains mounted.

