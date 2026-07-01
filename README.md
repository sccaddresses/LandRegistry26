# LandRegistry26

UK Land Registry Price Paid Data intelligence website.

This repository builds a static website that interrogates HM Land Registry Price Paid Data for user-defined postcode prefixes, regions, price bands, property types and time windows.

## What it does

- Downloads official HM Land Registry yearly Price Paid CSV files.
- Filters to configured postcode prefixes, default `BN`.
- Optionally keeps standard transactions only: `ppd_category_type == A`.
- Produces weekly, monthly and annual observations.
- Builds a deployable static website under `site/`.
- Deploys to Hostinger by SSH/rsync with FTP fallback.

## Quick start

1. Create a new GitHub repository, recommended name: `LandRegistry26`.
2. Upload this repository's contents.
3. Go to **Actions → LandRegistry Weekly Build and Deploy → Run workflow**.
4. Use:
   - `postcode_prefixes`: `BN`
   - `years`: `2025 2026`
   - `deploy`: `false`
5. Download the `landregistry-site` artifact and inspect the website.
6. Add Hostinger secrets and rerun with deploy enabled.

## Required deploy secrets

For SSH/rsync deployment:

- `LANDREGISTRY_SSH_HOST`
- `LANDREGISTRY_SSH_USERNAME`
- `LANDREGISTRY_SSH_PASSWORD`
- `LANDREGISTRY_SSH_PORT` optional, defaults to `22`
- `LANDREGISTRY_HOSTINGER_PUBLIC_HTML_DIR`

For FTP fallback:

- `LANDREGISTRY_FTP_HOST`
- `LANDREGISTRY_FTP_USERNAME`
- `LANDREGISTRY_FTP_PASSWORD`
- `LANDREGISTRY_FTP_PORT` optional, defaults to `21`
- `LANDREGISTRY_FTP_TARGET_DIR` optional

## Data policy

Large raw CSV/parquet files are not committed. The workflow downloads or rebuilds them as needed.

## Attribution

Contains HM Land Registry data © Crown copyright and database right. This data is licensed under the Open Government Licence v3.0.

## Deployment and email secrets

This repository expects the following GitHub Actions repository secrets for Hostinger SSH deployment:

```text
SCCLANDREGISTRY_HOSTINGER_PUBLIC_HTML_DIR
SCCLANDREGISTRY_SSH_HOST
SCCLANDREGISTRY_SSH_PORT
SCCLANDREGISTRY_SSH_USERNAME
SCCLANDREGISTRY_SSH_PASSWORD
```

Optional FTP fallback secrets:

```text
SCCLANDREGISTRY_FTP_HOST
SCCLANDREGISTRY_FTP_USERNAME
SCCLANDREGISTRY_FTP_PASSWORD
SCCLANDREGISTRY_FTP_PORT
SCCLANDREGISTRY_FTP_TARGET_DIR
```

Weekly email summary secrets:

```text
SMTP_LANDREGISTRY_FROM
SMTP_LANDREGISTRY_HOST
SMTP_LANDREGISTRY_PASSWORD
SMTP_LANDREGISTRY_PORT
SMTP_LANDREGISTRY_USER
SMTP_LANDREGISTRY_TO
```

`SMTP_LANDREGISTRY_TO` is optional. If it is not set, the weekly email is sent to `SMTP_LANDREGISTRY_FROM`.

The scheduled Monday run sends the email automatically. Manual workflow runs only send an email when the `send_email` input is set to `true`.
