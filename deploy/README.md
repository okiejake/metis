# Deploying Metis to a home server

Two environments on one machine, each with its own database, reached privately
over [Tailscale](https://tailscale.com). One runs at a time, which keeps a small
server comfortable:

| Environment | URL | Follows | Data |
| --- | --- | --- | --- |
| prod | `https://<server>.<tailnet>.ts.net/metis_prod` | the latest GitHub release | its own |
| qa | `https://<server>.<tailnet>.ts.net/metis_qa` | `main` | a copy of prod's, taken when you switch to it |

GitHub builds the image (`.github/workflows/image.yml`), so the server only
downloads and runs it. That suits a small NAS. Nothing is exposed to the
internet: the apps listen on the server's own loopback address, and Tailscale
Serve is the only way in.

Written for a Synology NAS with Container Manager. Any Linux machine with Docker
and Tailscale works the same way. Run the commands below on the server as root.

## 1. Before you start

- Tailscale is installed on the server and signed in to your tailnet.
- HTTPS certificates are enabled for the tailnet (admin console → DNS → HTTPS
  Certificates). Machine names become visible in public certificate logs, so
  name the server something you're happy to publish.
- Docker with Compose v2 (Container Manager provides both).
- SSH access to the server.

## 2. Install

Copy this folder to the server, for example to `/volume1/docker/metis`, then:

```sh
cd /volume1/docker/metis
cp .env.example .env      # adjust if you need other ports or paths
./metis.sh update         # downloads the images and starts prod
```

`./metis.sh status` should show `prod` running. Its first start creates the
database, which can take a few minutes on a small NAS.

## 3. Serve it over Tailscale

```sh
tailscale serve --bg --set-path /metis_prod http://127.0.0.1:8101
tailscale serve --bg --set-path /metis_qa http://127.0.0.1:8102
tailscale serve status
```

On a Synology the `tailscale` command is at
`/var/packages/Tailscale/target/bin/tailscale`. The paths and ports must match
`.env`. Open `https://<server>.<tailnet>.ts.net/metis_prod` and create your
login. The environment that isn't running answers with an error page.

## 4. Schedule updates and backups

Two tasks, both run as root:

| Task | When | Command |
| --- | --- | --- |
| metis update | every 15 minutes | `/volume1/docker/metis/metis.sh update >> /volume1/docker/metis/update.log 2>&1` |
| metis backup | daily at 3:00 | `/volume1/docker/metis/metis.sh backup >> /volume1/docker/metis/update.log 2>&1` |

`update` is quiet unless something changed. When the running environment has a
new image it restarts it, backing up prod first, and checks that it answers. A
stopped environment only downloads; it starts on the new image next time.
`backup` keeps the newest `KEEP_BACKUPS` copies of prod's database in
`backups/`, pausing prod for a few seconds if it is running.

On a Synology: Control Panel → Task Scheduler → Create → Scheduled Task →
User-defined script, user `root`, with the schedule and command above.

A GitHub webhook can't reach a server that is only on your tailnet, which is
why the server checks instead.

## 5. Switch between prod and qa

```sh
./metis.sh qa     # stop prod, copy prod's data to qa, start qa
./metis.sh prod   # stop qa, back up prod, start prod
```

qa follows `main`, so a merged change is ready to try within minutes of GitHub
finishing its build. Changes made in qa never reach prod's data. Whichever
environment is running comes back after the server restarts.

## Settings

All in `.env`, which stays on the server. See `.env.example` for each one.

| Setting | Purpose |
| --- | --- |
| `PROD_PATH`, `QA_PATH` | Path each environment is served under (`METIS_BASE_PATH` in the app) |
| `PROD_PORT`, `QA_PORT` | Loopback port each environment listens on |
| `PROD_TAG`, `QA_TAG` | Image tag each environment follows |
| `METIS_MEM_LIMIT` | Memory cap per container |
| `METIS_DB_MEMORY_LIMIT`, `METIS_DB_THREADS` | Database caps inside the container |
| `METIS_SECURE_COOKIES` | `1` when served over HTTPS |
| `KEEP_BACKUPS` | How many prod backups to keep |

## Restore a backup

```sh
docker compose stop prod
cp backups/finance-<stamp>.duckdb prod/finance.duckdb
rm -f prod/finance.duckdb.wal
docker compose up -d prod
```
