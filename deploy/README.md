# Deploying fastcad

A minimal-stack production deploy: one Ubuntu 24.04 VM running Caddy as
the only public surface, fastcad on loopback behind it, fail2ban
watching the access log. No container runtime, no managed-service
dependencies, no Cloudflare in front. The only third-party
infrastructure is the cloud you provision the VM in and Let's Encrypt
for the cert.

## What's in here

| File | Where it lives on the VM | Purpose |
|------|--------------------------|---------|
| `Caddyfile`                       | `/etc/caddy/Caddyfile`              | TLS, basic_auth, rate limit, hardening headers, WS reverse-proxy. Placeholders are filled by `bootstrap.sh`. |
| `fastcad.service`                 | `/etc/systemd/system/fastcad.service` | Hardened systemd unit (sandboxed, resource-capped, runs as `fastcad` user). |
| `fail2ban-jail.local`             | `/etc/fail2ban/jail.local`          | Bans IPs after 5 × 401 in 10 min on `/var/log/caddy/access.log`. |
| `fail2ban-filter-caddy-auth.conf` | `/etc/fail2ban/filter.d/caddy-auth.conf` | Regex over the JSON access-log lines. |
| `fastcad-cleanup.cron`            | `/etc/cron.d/fastcad-cleanup`       | Weekly prune of `tmp/` artifacts older than 7 days. |
| `bootstrap.sh`                    | run once on the VM                   | One-shot installer: apt + npm + venv + render Caddyfile + start services. Idempotent. |

The fastcad app itself is *not* containerised. It runs from
`/opt/fastcad/` as a venv, exactly like the dev environment, so any
change you can develop locally with `bash scripts/dev.sh` deploys with
a `git pull` + `systemctl restart fastcad`.

## Provisioning the VM (GCP — minimal command set)

These commands run from your laptop. You'll need `gcloud` installed
and `gcloud auth login` already done. Adjust `PROJECT`, `ZONE`, `NAME`
to taste.

```sh
PROJECT=my-project
ZONE=us-central1-a            # an always-free zone
NAME=fastcad

gcloud compute instances create "$NAME" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --machine-type=e2-small \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=20GB \
  --boot-disk-type=pd-balanced \
  --tags=http-server,https-server \
  --shielded-secure-boot \
  --shielded-vtpm \
  --shielded-integrity-monitoring

# Reserve a static external IP so the DNS record / nip.io hostname
# stays valid across reboots.
gcloud compute addresses create "${NAME}-ip" \
  --project="$PROJECT" --region="${ZONE%-*}"
IP=$(gcloud compute addresses describe "${NAME}-ip" \
  --project="$PROJECT" --region="${ZONE%-*}" --format='value(address)')
gcloud compute instances add-access-config "$NAME" \
  --project="$PROJECT" --zone="$ZONE" \
  --access-config-name="external-nat" --address="$IP"

# Open the public ports. (Tighten SSH later: ssh-from-iap-only.)
gcloud compute firewall-rules create "${NAME}-web" \
  --project="$PROJECT" \
  --network=default \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:80,tcp:443 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=http-server,https-server
```

The `e2-small` choice (~$13/mo) gives 2 GB of RAM, comfortably
covering OpenSCAD's render footprint plus the agent's memory cap.
Drop to `e2-micro` if your workload is light; that fits the always-
free tier.

> **Equivalent on Azure:** `az vm create --image Ubuntu2404 --size
> Standard_B1s --admin-username fastcad --generate-ssh-keys` plus
> `az network nsg rule create` for ports 80/443.
>
> **Equivalent on AWS:** `aws ec2 run-instances --image-id <ami> --instance-type t3.small`
> plus security-group ingress on 80/443.

## Hostname

Two paths from cheapest to nicest:

1. **`<ip-with-dashes>.nip.io`** — free, no DNS work. e.g. for
   `IP=34.72.1.2` your hostname is `34-72-1-2.nip.io`. Caddy issues a
   real Let's Encrypt cert against it. Fine for personal / demo use.
2. **A real subdomain you own** — point an A record at `IP`, wait
   for propagation (usually a couple of minutes), then use
   `fastcad.example.com` as `HOST`.

## Bootstrap on the VM

SSH in, then:

```sh
sudo bash -c '
  cd /opt
  git clone https://github.com/adi-lumenorbit/fastcad.git
  bash fastcad/deploy/bootstrap.sh
'
```

`bootstrap.sh` will prompt for:
- Hostname
- ACME email (Let's Encrypt registration)
- basic_auth username
- Anthropic API key

It generates a random 32-char password, prints it once, and stores it
in `/etc/fastcad/deploy.env` (mode 0600, root-only). The Caddyfile
ends up with only the bcrypt hash — the plaintext is never on the
serving path.

When it returns:
- `https://<host>/` is live with HTTPS + basic_auth.
- `journalctl -fu fastcad` streams app logs.
- `fail2ban-client status caddy-auth` shows banned-IP state.

## Operational cheatsheet

```sh
# Roll the API key
sudo nano /etc/fastcad/fastcad.env  # update ANTHROPIC_API_KEY=
sudo systemctl restart fastcad

# Roll the basic_auth password
sudo rm /etc/fastcad/deploy.env
sudo bash /opt/fastcad/deploy/bootstrap.sh

# Pick up a new code release
sudo -u fastcad git -C /opt/fastcad pull
sudo /opt/fastcad/.venv/bin/pip install -e /opt/fastcad
sudo systemctl restart fastcad

# Free disk by pruning render dumps
sudo -u fastcad find /opt/fastcad/tmp -mindepth 1 -mtime +7 -delete
```

## What's NOT in this deploy

- **Container runtime** (Docker/Podman). Bare VM keeps the dep set
  small; `OpenSCAD` and `claude` install cleanly with apt + npm.
- **Cloudflare / WAF**. Caddy + fail2ban + the per-session
  rate-limiting in the app itself cover ordinary abuse vectors. Add
  Cloudflare later if you ever face directed L7 attacks.
- **Secret Manager / KeyVault**. The API key sits in
  `/etc/fastcad/fastcad.env` with mode 0600, owned by root.
  Sufficient for a single-user host; if you ever multi-tenant the
  service, swap to GCP Secret Manager + systemd `LoadCredential=`.
- **Multi-instance / load balancer**. fastcad's session is in-memory
  per WebSocket; a single instance is the right scale.

See `docs/architecture.md` for the full system view.
