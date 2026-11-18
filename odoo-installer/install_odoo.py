#!/usr/bin/env python3
"""
Odoo Installer (Ubuntu 22.04+)
Supports: Odoo 17+ Community / Enterprise
"""

import argparse, json, os, re, secrets, shutil, subprocess, sys
from pathlib import Path

PYTHON_VERSION_RE = re.compile(r"^3\.\d+$")

WKHTMLTOPDF_URL = (
    "https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/"
    "wkhtmltox_0.12.6.1-2.jammy_amd64.deb"
)

def run(cmd, check=True):
    print(">", cmd)
    subprocess.run(cmd, shell=True, check=check)

def install_wkhtmltopdf():
    run("command -v wkhtmltopdf >/dev/null 2>&1 && exit 0; "
        f"curl -fsSL -o /tmp/wkhtmltox.deb {WKHTMLTOPDF_URL} && "
        "apt-get install -y /tmp/wkhtmltox.deb && "
        "rm -f /tmp/wkhtmltox.deb")

def backup_file(path):
    p = Path(path)
    if p.exists():
        shutil.copy2(p, str(p) + ".bak")

def randpwd():
    return secrets.token_urlsafe(24)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, type=int)
    ap.add_argument("--edition", choices=["community","enterprise"], required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--github-user")
    ap.add_argument("--github-token")
    ap.add_argument("--port", type=int, default=8069)
    ap.add_argument("--python-version", default=None,
                     help="Python version to install and use for Odoo's venv, e.g. 3.8 or 3.11. "
                          "Defaults to the distro's system python3. A non-default version is "
                          "installed from the deadsnakes PPA.")
    ap.add_argument("--with-rtl", action="store_true",
                     help="Install rtlcss for right-to-left language support (Arabic, Hebrew, etc.)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.python_version and not PYTHON_VERSION_RE.match(args.python_version):
        raise SystemExit(
            f"--python-version must be major.minor, e.g. 3.8 or 3.11 "
            f"(got {args.python_version!r})"
        )

    v = args.version
    base = f"/opt/odoo{v}"
    db_user = f"odoo{v}"
    db_pass = randpwd()
    admin_pass = randpwd()

    pyver = args.python_version
    python_bin = f"python{pyver}" if pyver else "python3"

    cmds = [
        "apt-get update",
    ]

    if pyver:
        cmds.append(
            "apt-get install -y software-properties-common && "
            "add-apt-repository -y ppa:deadsnakes/ppa && apt-get update"
        )
        cmds.append(
            f"apt-get install -y git curl wget build-essential python{pyver} python{pyver}-dev "
            f"python{pyver}-venv python3-pip "
            "libxml2-dev libxslt1-dev libldap2-dev libsasl2-dev libpq-dev libjpeg-dev "
            "libffi-dev libssl-dev zlib1g-dev postgresql nginx npm "
            "xfonts-75dpi xfonts-base fontconfig libjpeg-turbo8 libxrender1"
        )
    else:
        cmds.append(
            "apt-get install -y git curl wget build-essential python3-dev python3-pip python3-venv "
            "libxml2-dev libxslt1-dev libldap2-dev libsasl2-dev libpq-dev libjpeg-dev "
            "libffi-dev libssl-dev zlib1g-dev postgresql nginx npm "
            "xfonts-75dpi xfonts-base fontconfig libjpeg-turbo8 libxrender1"
        )

    npm_cmds = []
    if args.with_rtl:
        npm_cmds.append("npm install -g rtlcss")

    pip_cmds = [
        "pip3 install libsass",
    ]

    if args.dry_run:
        print("DRY RUN")
        for c in cmds: print(c)
        print(f"curl -fsSL -o /tmp/wkhtmltox.deb {WKHTMLTOPDF_URL} && apt-get install -y /tmp/wkhtmltox.deb")
        for c in npm_cmds: print(c)
        for c in pip_cmds: print(c)
        return

    for c in cmds:
        run(c)

    install_wkhtmltopdf()

    for c in npm_cmds:
        run(c)

    run("id -u odoo >/dev/null 2>&1 || adduser --system --home=/opt/odoo --group odoo", check=False)

    run(f"sudo -u postgres psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='{db_user}'\" | grep -q 1 || "
        f"sudo -u postgres psql -c \\\"CREATE ROLE {db_user} LOGIN CREATEDB PASSWORD '{db_pass}';\\\"",
        check=False)

    Path(base).mkdir(parents=True, exist_ok=True)
    run(f"git clone --depth 1 --branch {v}.0 https://github.com/odoo/odoo.git {base}", check=False)

    Path(f"{base}/custom/addons").mkdir(parents=True, exist_ok=True)

    addons = f"{base}/addons,{base}/custom/addons"

    if args.edition == "enterprise":
        if not args.github_user or not args.github_token:
            raise SystemExit("Enterprise requires --github-user and --github-token")
        run(
            f"git clone --depth 1 --branch {v}.0 "
            f"https://{args.github_user}:{args.github_token}@github.com/odoo/enterprise.git "
            f"{base}/custom/enterprise",
            check=False
        )
        addons += f",{base}/custom/enterprise"

    run(f"{python_bin} -m venv {base}/venv")
    run(f"{base}/venv/bin/pip install wheel")
    run(f"{base}/venv/bin/pip install -r {base}/requirements.txt")
    for c in pip_cmds:
        run(f"{base}/venv/bin/{c}")

    Path("/etc/odoo").mkdir(exist_ok=True)

    conf = f"/etc/odoo/odoo{v}.conf"
    backup_file(conf)
    Path(conf).write_text(f"""
[options]
admin_passwd = {admin_pass}
db_host = localhost
db_port = 5432
db_user = {db_user}
db_password = {db_pass}
addons_path = {addons}
logfile = /var/log/odoo/odoo{v}.log
proxy_mode = True
xmlrpc_port = {args.port}
""".strip())

    Path("/var/log/odoo").mkdir(exist_ok=True)

    service = f"/etc/systemd/system/odoo{v}.service"
    backup_file(service)
    Path(service).write_text(f"""
[Unit]
Description=Odoo {v}
After=postgresql.service

[Service]
Type=simple
User=odoo
Group=odoo
ExecStart={base}/venv/bin/python3 {base}/odoo-bin -c {conf}
Restart=always

[Install]
WantedBy=multi-user.target
""".strip())

    nginx = f"/etc/nginx/sites-available/odoo{v}"
    Path(nginx).write_text(f"""
upstream odoo{v} {{
 server 127.0.0.1:{args.port};
}}
server {{
 server_name {args.domain};
 location / {{
   proxy_pass http://odoo{v};
   proxy_set_header Host $host;
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   proxy_set_header X-Forwarded-Proto $scheme;
 }}
}}
""".strip())

    enabled = f"/etc/nginx/sites-enabled/odoo{v}"
    if not os.path.exists(enabled):
        os.symlink(nginx, enabled)

    run("systemctl daemon-reload")
    run(f"systemctl enable odoo{v}")
    run(f"systemctl restart odoo{v}")
    run("nginx -t")
    run("systemctl reload nginx")

    creds = {
        "version": v,
        "edition": args.edition,
        "domain": args.domain,
        "db_user": db_user,
        "db_password": db_pass,
        "admin_password": admin_pass,
    }

    Path(f"/root/odoo{v}_installation.json").write_text(json.dumps(creds, indent=2))
    Path(f"/root/odoo{v}_credentials.txt").write_text(
        "\\n".join(f"{k}: {v}" for k,v in creds.items())
    )

    print("Installation completed")

if __name__ == "__main__":
    main()
