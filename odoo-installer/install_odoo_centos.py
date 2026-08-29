#!/usr/bin/env python3
"""
Odoo Installer (CentOS 7.9)
Supports: Odoo 15 Community / Enterprise
"""

import argparse, json, os, secrets, shutil, subprocess, sys
from pathlib import Path

WKHTMLTOPDF_URL = (
    "https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/"
    "wkhtmltox-0.12.6.1-2.centos7.x86_64.rpm"
)

SCL_PYTHON = "/opt/rh/rh-python38/root/usr/bin/python3.8"

def run(cmd, check=True):
    print(">", cmd)
    subprocess.run(cmd, shell=True, check=check)

def install_wkhtmltopdf():
    run("command -v wkhtmltopdf >/dev/null 2>&1 && exit 0; "
        f"curl -fsSL -o /tmp/wkhtmltox.rpm {WKHTMLTOPDF_URL} && "
        "yum localinstall -y /tmp/wkhtmltox.rpm && "
        "rm -f /tmp/wkhtmltox.rpm")

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
    ap.add_argument("--with-rtl", action="store_true",
                     help="Install rtlcss for right-to-left language support (Arabic, Hebrew, etc.)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    v = args.version
    base = f"/opt/odoo{v}"
    db_user = f"odoo{v}"
    db_pass = randpwd()
    admin_pass = randpwd()

    cmds = [
        "yum install -y epel-release",
        "yum install -y centos-release-scl",
        "yum install -y git curl wget gcc gcc-c++ make rh-python38 rh-python38-python-devel "
        "libxml2-devel libxslt-devel openldap-devel cyrus-sasl-devel postgresql-devel "
        "libjpeg-turbo-devel libffi-devel openssl-devel zlib-devel nginx "
        "xorg-x11-fonts-75dpi xorg-x11-fonts-Type1 fontconfig libXrender libXext",

        "curl -fsSL https://rpm.nodesource.com/setup_lts.x | bash -",
        "yum install -y nodejs",

        "yum install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-7-x86_64/pgdg-redhat-repo-latest.noarch.rpm",
        "yum install -y postgresql13-server postgresql13",
        "test -f /var/lib/pgsql/13/data/PG_VERSION || /usr/pgsql-13/bin/postgresql-13-setup initdb",
        "systemctl enable postgresql-13",
        "systemctl start postgresql-13",
    ]

    npm_cmds = []
    if args.with_rtl:
        npm_cmds.append("npm install -g rtlcss")

    pip_cmds = [
        f"{SCL_PYTHON} -m pip install --upgrade pip wheel",
        f"scl enable rh-python38 -- pip3 install libsass",
    ]

    if args.dry_run:
        print("DRY RUN")
        for c in cmds: print(c)
        print(f"curl -fsSL -o /tmp/wkhtmltox.rpm {WKHTMLTOPDF_URL} && yum localinstall -y /tmp/wkhtmltox.rpm")
        for c in npm_cmds: print(c)
        for c in pip_cmds: print(c)
        return

    for c in cmds:
        run(c)

    install_wkhtmltopdf()

    for c in npm_cmds:
        run(c)

    for c in pip_cmds:
        run(c)

    run("id -u odoo >/dev/null 2>&1 || adduser --system --home-dir=/opt/odoo --shell=/sbin/nologin --user-group odoo", check=False)

    run(f"sudo -u postgres /usr/pgsql-13/bin/psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='{db_user}'\" | grep -q 1 || "
        f"sudo -u postgres /usr/pgsql-13/bin/psql -c \\\"CREATE ROLE {db_user} LOGIN CREATEDB PASSWORD '{db_pass}';\\\"",
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

    run(f"{SCL_PYTHON} -m venv {base}/venv")
    run(f"{base}/venv/bin/pip install wheel")
    run(f"{base}/venv/bin/pip install -r {base}/requirements.txt")

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
    run("chown -R odoo:odoo /var/log/odoo", check=False)
    run(f"chown -R odoo:odoo {base}", check=False)

    service = f"/etc/systemd/system/odoo{v}.service"
    backup_file(service)
    Path(service).write_text(f"""
[Unit]
Description=Odoo {v}
After=postgresql-13.service

[Service]
Type=simple
User=odoo
Group=odoo
ExecStart={base}/venv/bin/python3 {base}/odoo-bin -c {conf}
Restart=always

[Install]
WantedBy=multi-user.target
""".strip())

    nginx = f"/etc/nginx/conf.d/odoo{v}.conf"
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

    # SELinux: allow nginx to proxy_pass to the local Odoo port
    run("command -v setsebool >/dev/null 2>&1 && setsebool -P httpd_can_network_connect 1", check=False)

    # firewalld: open the Odoo port and allow http for nginx
    run(f"command -v firewall-cmd >/dev/null 2>&1 && "
        f"firewall-cmd --permanent --add-port={args.port}/tcp && "
        f"firewall-cmd --permanent --add-service=http && "
        f"firewall-cmd --reload", check=False)

    run("systemctl daemon-reload")
    run(f"systemctl enable odoo{v}")
    run(f"systemctl restart odoo{v}")
    run("systemctl enable nginx")
    run("nginx -t")
    run("systemctl restart nginx")

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
        "\n".join(f"{k}: {v}" for k,v in creds.items())
    )

    print("Installation completed")

if __name__ == "__main__":
    main()
