# Odoo Installer

A single-file Python script that installs and configures [Odoo](https://www.odoo.com/) (Community or Enterprise) on Ubuntu 22.04+, complete with PostgreSQL, Nginx reverse proxy, and a systemd service.

## What it does

Running the script performs the following steps:

1. Installs system dependencies (`git`, `build-essential`, Python dev headers, PostgreSQL, Nginx, etc.) via `apt-get`.
2. Installs the official patched-Qt build of `wkhtmltopdf` (required by Odoo for PDF report generation), if not already installed.
3. Creates a system user named `odoo`.
4. Creates a PostgreSQL role for the Odoo instance with a randomly generated password.
5. Clones the requested Odoo version from GitHub into `/opt/odoo<version>`.
6. Clones the Enterprise addons repo as well, if `--edition enterprise` is selected.
7. Creates a Python virtual environment and installs Odoo's `requirements.txt`.
8. Generates an Odoo config file at `/etc/odoo/odoo<version>.conf` with a random admin password and the database credentials.
9. Creates and enables a systemd service (`odoo<version>.service`) to run Odoo.
10. Creates an Nginx reverse proxy site for the given domain and enables it.
11. Reloads systemd/Nginx and starts the Odoo service.
12. Writes generated credentials to `/root/odoo<version>_installation.json` and `/root/odoo<version>_credentials.txt`.

Existing config/service files are backed up (`.bak`) before being overwritten.

## Requirements

- Ubuntu 22.04 (Jammy) on amd64 — the `wkhtmltopdf` `.deb` downloaded by the script is built specifically for `jammy`/`amd64`
- Run as `root` (or with `sudo`), since it installs packages, creates system users, and writes to `/etc`, `/opt`, and `/var/log`
- Internet access to clone from GitHub, install apt packages, and download the `wkhtmltopdf` release from GitHub Releases
- A GitHub account with access to the `odoo/enterprise` repo if installing the Enterprise edition

## Usage

```bash
sudo python3 install_odoo.py --version 17 --edition community --domain odoo.example.com
```

### Enterprise edition

Enterprise requires GitHub credentials with access to the private `odoo/enterprise` repository:

```bash
sudo python3 install_odoo.py \
  --version 17 \
  --edition enterprise \
  --domain odoo.example.com \
  --github-user your-github-username \
  --github-token your-github-token
```

### Dry run

Preview the apt packages that would be installed without making any changes:

```bash
python3 install_odoo.py --version 17 --edition community --domain odoo.example.com --dry-run
```

## Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--version` | Yes | — | Odoo major version to install (e.g. `17`) |
| `--edition` | Yes | — | `community` or `enterprise` |
| `--domain` | Yes | — | Domain name used in the Nginx `server_name` |
| `--github-user` | Only for Enterprise | — | GitHub username with access to `odoo/enterprise` |
| `--github-token` | Only for Enterprise | — | GitHub personal access token |
| `--port` | No | `8069` | Local port Odoo listens on (proxied by Nginx) |
| `--dry-run` | No | `False` | Print the apt install commands and exit without changing anything |

## What gets created

| Path | Purpose |
|---|---|
| `/opt/odoo<version>/` | Odoo source, virtualenv, and custom/enterprise addons |
| `/etc/odoo/odoo<version>.conf` | Odoo configuration file |
| `/etc/systemd/system/odoo<version>.service` | systemd unit for the Odoo service |
| `/etc/nginx/sites-available/odoo<version>` | Nginx site config (symlinked into `sites-enabled`) |
| `/var/log/odoo/odoo<version>.log` | Odoo log file |
| `/root/odoo<version>_installation.json` | Generated credentials (JSON) |
| `/root/odoo<version>_credentials.txt` | Generated credentials (plain text) |

## wkhtmltopdf

Odoo uses `wkhtmltopdf` to render PDF reports (invoices, quotations, etc.). Odoo requires the **patched-Qt** build (`0.12.6.1-2`), not the version in Ubuntu's default apt repositories — the stock apt package is unpatched and produces broken headers, footers, and page breaks in reports.

The script downloads the official patched `.deb` for Ubuntu 22.04 (Jammy, amd64) from the [wkhtmltopdf packaging releases](https://github.com/wkhtmltopdf/packaging/releases) and installs it with `apt-get install`, which also resolves its runtime dependencies (fonts, `libjpeg-turbo8`, `libxrender1`, etc.). If `wkhtmltopdf` is already on `PATH`, this step is skipped.

## Multiple instances

Because paths, service names, and the database user are all suffixed with `--version`, you can run the script multiple times with different `--version` values to run several Odoo versions side by side on the same host (using different `--port` values and domains).

## Security notes

- Database and admin passwords are generated with `secrets.token_urlsafe(24)` and saved to root-only-readable files under `/root/`. Move or secure these files as appropriate for your environment.
- The Nginx config generated here does not set up TLS/SSL. Use a tool like [Certbot](https://certbot.eff.org/) afterward to enable HTTPS for the configured domain.
- The script must be run as root and makes system-wide changes (installs packages, writes systemd units, modifies Nginx sites-enabled) — review the script before running it on a production or shared host.
