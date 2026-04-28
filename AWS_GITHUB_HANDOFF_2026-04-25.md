# AWS + GitHub Auto-Deploy Handoff (2026-04-25)

## What you are trying to achieve

- Push to `main` should automatically deploy backend API.
- Push to `main` should automatically deploy frontend.
- No manual EC2 commands for normal deploys.
- Keep security tight and understandable.

This handoff is designed to stop the loop of random errors by using one simple deployment model:

- Backend: EC2 + systemd + GitHub self-hosted runner
- Frontend: S3 static hosting + CloudFront + GitHub OIDC deploy workflow

## Final target architecture

- `main` push triggers two independent workflows/jobs:
  - Backend deploy on EC2 self-hosted runner:
    - `git pull`
    - `pip install -r requirements.txt`
    - `systemctl restart vibe-check`
  - Frontend deploy on GitHub-hosted runner:
    - sync `web/` to S3 bucket
    - invalidate CloudFront cache
- Browser loads frontend from CloudFront URL.
- Frontend calls API on EC2 domain/IP (through Caddy).

## Current repo status (already in place)

- Backend workflow exists at `.github/workflows/ci-cd.yml` and deploy job is `runs-on: self-hosted`.
- That means backend auto-deploy works once the EC2 runner is healthy and registered.

---

## One-time setup checklist

Complete these once. After this, deploys are commit-only.

### 1) EC2 base setup (backend host)

1. Launch Ubuntu EC2 instance.
2. Security Group inbound:
   - `80` from `0.0.0.0/0`
   - `443` from `0.0.0.0/0` (can add now or later)
   - `22` only if you need SSH; prefer SSM and remove `22` later
3. Attach IAM role with `AmazonSSMManagedInstanceCore`.
4. Install and configure app once:
   - clone repo to `/home/ubuntu/vibe-check`
   - create venv and install requirements
   - create `.env`
   - create `vibe-check.service`
   - enable/start service
5. Configure Caddy to reverse proxy `:80` to `127.0.0.1:8000`.

### 2) GitHub self-hosted runner on EC2 (backend auto-deploy)

1. In GitHub repo: Settings -> Actions -> Runners -> New self-hosted runner.
2. Run the generated Linux commands on EC2.
3. Install runner as service:

```bash
cd /home/ubuntu/actions-runner
sudo ./svc.sh install ubuntu
sudo ./svc.sh start
```

4. Allow restart command without interactive sudo prompt:

```bash
echo "ubuntu ALL=(ALL) NOPASSWD: /bin/systemctl restart vibe-check" | sudo tee /etc/sudoers.d/vibe-check
```

### 3) Frontend hosting in AWS (S3 + CloudFront)

1. Create private S3 bucket for frontend files, e.g. `vibe-check-web-prod`.
2. Create CloudFront distribution with S3 bucket as origin.
3. Use Origin Access Control (OAC), keep bucket private.
4. Add `index.html` as default root object.
5. Record:
   - S3 bucket name
   - CloudFront distribution ID
   - AWS region

### 4) GitHub OIDC role for frontend deploy

Create IAM role trusted by GitHub OIDC for this repo/branch.

Required permissions for that role:

- `s3:ListBucket` on frontend bucket
- `s3:PutObject`, `s3:DeleteObject` on frontend bucket objects
- `cloudfront:CreateInvalidation` on your distribution

Trust policy should restrict at least:

- repository = your repo
- branch = `refs/heads/main`
- audience = `sts.amazonaws.com`

### 5) GitHub repo variables for frontend workflow

Set repository Variables (not secrets unless you prefer):

- `FRONTEND_AWS_ROLE_ARN`
- `FRONTEND_AWS_REGION`
- `FRONTEND_S3_BUCKET`
- `FRONTEND_CLOUDFRONT_DIST_ID`
- `FRONTEND_API_BASE_URL` (example: `http://YOUR_EC2_IP` or `https://api.yourdomain.com`)

---

## Normal deploy flow (after one-time setup)

1. Commit and push to `main`.
2. GitHub Actions runs tests.
3. Backend deploy job runs on EC2 self-hosted runner.
4. Frontend deploy workflow syncs `web/` to S3 and invalidates CloudFront.
5. Done. No manual EC2 work required.

---

## Security concerns: temporary vs keep vs reversible

### A) One-time or temporary actions

- Opening port `22` to world for bootstrap/debug.
  - Risk: larger attack surface.
  - Reversible: yes, remove or restrict after setup.

- Manual EC2 shell setup steps.
  - Risk: command mistakes.
  - Reversible: mostly yes (service files, config edits), except leaked secrets.

### B) Keep permanently (good practice)

- Keep EC2 app bound to `127.0.0.1` only; expose only through Caddy.
- Keep `.env` out of git.
- Keep Actions deploy permissions narrow (least privilege).
- Keep OIDC over static AWS keys for GitHub deploy.
- Keep branch protection on `main`.

### C) Reversible hardening after first green deploy

- Remove inbound `22` if using SSM only.
- Remove old/unused IAM roles and GitHub variables from previous SSM/OIDC experiments.
- Rotate `ADMIN_TOKEN` after stabilization.
- If any AWS access keys were created for testing, delete them.

### D) Self-hosted runner risk note

A self-hosted runner executes workflow code from your repo. Protect `main` and PR merge rules so untrusted workflow changes cannot run on your EC2 host.

---

## Fast troubleshooting (only when pipeline breaks)

### Backend job fails

Check on EC2:

```bash
sudo ./svc.sh status || true
sudo systemctl status vibe-check --no-pager
journalctl -u vibe-check -n 100 --no-pager
```

Common causes:

- runner offline
- dependency install failed
- `.env` missing required values
- systemd restart permission missing in sudoers

### Frontend job fails

Common causes:

- incorrect role ARN or region
- OIDC trust policy does not match repo/branch
- missing S3/CloudFront IAM permissions
- wrong bucket/distribution ID variables
- missing `FRONTEND_API_BASE_URL` when frontend and API are on different origins

---

## Definition of done

You are done when all are true:

1. Push to `main` triggers green backend deploy automatically.
2. Push changing `web/` triggers green frontend deploy automatically.
3. API available at EC2/Caddy URL.
4. Frontend available at CloudFront URL and can call API.
5. No manual EC2 commands needed for routine deploy.

---

## Optional next hardening (after stable deploy)

- Add domain + TLS for API via Caddy.
- Put API behind domain and update CORS to exact CloudFront/domain origin.
- Add CloudWatch alarms for EC2 health and basic uptime monitoring.
