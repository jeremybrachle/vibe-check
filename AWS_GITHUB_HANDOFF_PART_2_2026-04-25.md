# AWS + GitHub Auto-Deploy Handoff — part 2 (2026-04-25)

This is your tomorrow runbook based on the latest code and workflow changes.

What changed in code since part 1:
- Frontend now supports a runtime API base URL via `web/runtime-config.js`.
- Frontend deploy workflow now fails fast if required GitHub Variables are missing.
- Frontend deploy workflow now generates `web/runtime-config.js` with `FRONTEND_API_BASE_URL` at deploy time.

---

## Goal for tomorrow

By end of session, all of this should be true:

1. Push to `main` deploys backend on EC2 via self-hosted runner.
2. Push to `main` (with web changes) deploys frontend to S3 + CloudFront.
3. CloudFront frontend successfully calls backend API (no relative-path mismatch).
4. No manual EC2 commands needed for routine deploys.

---

## 0) Preflight facts you should have handy

Prepare these values before touching anything:

- GitHub repo owner/name
- EC2 public IP or API domain
- CloudFront domain
- Frontend S3 bucket name
- CloudFront distribution ID
- AWS region
- IAM role ARN for GitHub OIDC frontend deploy

---

## 1) Backend host sanity checks (EC2)

Run on EC2:

```bash
sudo systemctl status vibe-check --no-pager
sudo systemctl status caddy --no-pager
sudo /home/ubuntu/actions-runner/svc.sh status || true
```

Expected:
- `vibe-check` active/running
- `caddy` active/running
- runner service active/running

If `vibe-check` is not healthy:

```bash
journalctl -u vibe-check -n 120 --no-pager
```

Common fix areas:
- missing `/home/ubuntu/vibe-check/.env`
- broken Python venv at `/home/ubuntu/vibe-check/.venv`
- bad dependency install

---

## 2) Confirm backend deployment permissions

Runner user must restart the app without interactive sudo.

```bash
sudo cat /etc/sudoers.d/vibe-check
```

Expected line:

```text
ubuntu ALL=(ALL) NOPASSWD: /bin/systemctl restart vibe-check
```

---

## 3) Set required GitHub Variables (frontend)

In GitHub:
Settings -> Secrets and variables -> Actions -> Variables

Required Variables:

- `FRONTEND_AWS_ROLE_ARN`
- `FRONTEND_AWS_REGION`
- `FRONTEND_S3_BUCKET`
- `FRONTEND_CLOUDFRONT_DIST_ID`
- `FRONTEND_API_BASE_URL`

Set `FRONTEND_API_BASE_URL` to your backend public base URL:
- Example IP setup: `http://54.80.161.59`
- Example domain setup: `https://api.yourdomain.com`

Do not include a trailing slash.

---

## 4) Verify OIDC trust + permissions (frontend role)

Trust policy must restrict to:
- your repo
- branch `refs/heads/main`
- audience `sts.amazonaws.com`

Role permissions must include:
- `s3:ListBucket` on frontend bucket
- `s3:PutObject`, `s3:DeleteObject` on bucket objects
- `cloudfront:CreateInvalidation` on your distribution

---

## 5) Backend CORS for CloudFront origin

On EC2, set exact frontend origin in `.env`.

Example:

```env
ALLOWED_ORIGINS=https://dxxxxxxxxxxxx.cloudfront.net
```

If you have multiple frontends:

```env
ALLOWED_ORIGINS=https://dxxxxxxxxxxxx.cloudfront.net,https://your-other-frontend.com
```

Restart backend after edits:

```bash
sudo systemctl restart vibe-check
```

---

## 6) Trigger deployment cleanly

From local repo:

```bash
git add .github/workflows/frontend-deploy.yml web/app.js web/index.html web/runtime-config.js AWS_GITHUB_HANDOFF_part_2_2026-04-25.md
git commit -m "Deploy hardening: runtime API base config + frontend variable validation + part 2 handoff"
git push origin main
```

---

## 7) What to check in GitHub Actions

### Workflow A: CI and Deploy

- `unit-tests` must pass
- `deploy-aws` must run on self-hosted and pass

If backend deploy fails, inspect EC2:

```bash
sudo /home/ubuntu/actions-runner/svc.sh status || true
sudo systemctl status vibe-check --no-pager
journalctl -u vibe-check -n 120 --no-pager
```

### Workflow B: Frontend Deploy (S3 + CloudFront)

- variable validation step passes
- AWS OIDC credential step passes
- S3 sync step passes
- CloudFront invalidation passes

If it fails at validation, you are missing one of the required Variables.

---

## 8) Runtime config validation (new behavior)

After frontend deploy completes, verify `runtime-config.js` was deployed with your API base.

Quick check in browser dev tools on CloudFront site:

```js
window.VIBE_CONFIG
```

Expected shape:

```js
{ API_BASE: "http://54.80.161.59" }
```

If empty or missing, frontend calls may hit wrong origin.

---

## 9) End-to-end smoke checks

### Browser checks

1. Open CloudFront URL
2. Dashboard loads
3. No CORS errors in browser console
4. Data panels populate (latest digest/history)

### API checks from your machine

```bash
API_BASE=http://54.80.161.59 .venv/bin/python tests/smoke_test_live_api.py
```

Optional CORS-targeted check:

```bash
API_BASE=http://54.80.161.59 CORS_ORIGIN=https://dxxxxxxxxxxxx.cloudfront.net .venv/bin/python tests/smoke_test_live_api.py
```

---

## 10) Fast error map

Frontend shows but no data:
- Usually wrong `FRONTEND_API_BASE_URL` or backend CORS origin missing.

Frontend workflow fails before AWS auth:
- Missing required Variable(s).

Frontend workflow fails at AWS auth:
- OIDC trust policy mismatch (repo/branch/audience).

Backend workflow fails on restart:
- sudoers entry missing for systemctl restart.

Backend workflow fails on pip install:
- broken or missing `/home/ubuntu/vibe-check/.venv`.

---

## Definition of done for part 2

You are done when:

1. One push to `main` produces two green workflows.
2. CloudFront serves latest frontend build.
3. Frontend reads API successfully using runtime API base config.
4. Backend stays healthy after automated restart.
5. No manual EC2 deployment commands required for normal releases.
