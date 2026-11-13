# Deploying Metis to a home server

Two environments on one machine, each with its own database, reached privately
over [Tailscale](https://tailscale.com):

| Environment | URL | Runs | Follows |
| --- | --- | --- | --- |
| prod | `https://<server>.<tailnet>.ts.net/metis_prod` | always | the latest GitHub release |
| qa | `https://<server>.<tailnet>.ts.net/metis_qa` | when testing | `main` |

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

`./metis.sh status` should show `prod` running.

## 3. Serve it over Tailscale

```sh
tailscale serve --bg --set-path /metis_prod http://127.0.0.1:8101
tailscale serve --bg --set-path /metis_qa http://127.0.0.1:8102
tailscale serve status
```

On a Synology the `tailscale` command is at
`/var/packages/Tailscale/target/bin/tailscale`. The paths and ports must match
`.env`. Open `https://<server>.<tailnet>.ts.net/metis_prod` and create your
login. Each environment has its own users and data.

## 4. Keep prod up to date

Schedule `metis.sh update` every 15 minutes. It is quiet unless something
changed. When a new release is published it backs up prod's database to
`backups/`, restarts prod on the new image and checks that it answers.

On a Synology: Control Panel → Task Scheduler → Create → Scheduled Task →
User-defined script, user `root`, every 15 minutes, command:

```sh
/volume1/docker/metis/metis.sh update >> /volume1/docker/metis/update.log 2>&1
```

A GitHub webhook can't reach a server that is only on your tailnet, which is
why the server checks instead.

## 5. Test a change in qa

```sh
./metis.sh qa-start   # fresh copy of prod's data, then starts qa
./metis.sh qa-stop
```

`qa-start` pauses prod for a few seconds while it copies the database. qa
follows `main`, so a merged change is ready to try within minutes of GitHub
finishing its build. While qa is running, `update` keeps it current too.

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
