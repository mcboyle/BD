"""Edge deploy helpers (Phase 115, Block Q).

Generate deployment artifacts (Docker Compose, Kubernetes manifests,
systemd units) for the operator's BD instance + adjacent services.
Operators running BD on a NAS / homelab / Hetzner box often want to
codify their config — this module produces ready-to-apply files.

Three generators:

  • compose_yaml() — Docker Compose stack:
      bd + qbittorrent + flaresolverr + a VPN sidecar
      with named volumes, healthchecks, restart policies, and
      networks. Generated to fit the operator's needs (toggle each
      service via flags).

  • systemd_unit() — systemd service file for bare-metal hosts.
      Sets the right WorkingDirectory, environment vars, restart
      policy, and journal logging.

  • k8s_deployment() — minimal K8s Deployment + Service + PVC
      manifests. Good starting point; operators tune from there.

All output is plain text — caller can write to disk, print to stdout,
or stuff into a downloadable response. No side effects.

Inputs reach the text through _text (str only, no control characters --
a newline would add YAML keys or unit directives) and then a per-format
escape: _yq for YAML scalars, _unit_value/_unit_word/_unit_quoted for
systemd. Ports go through _port (int, 1-65535). Bad input is ValueError.
"""
from __future__ import annotations

import json
import re
from typing import Optional


# ─── Input checks + escaping ──────────────────────────────────────────

# A YAML plain scalar that stays a string under YAML 1.1 and 1.2: no
# leading digit/'.'/'-' (numbers, .inf), no ': ' / ' #' (no spaces at all),
# no flow or quote characters, and not a bool/null word.
_YAML_BARE = re.compile(r"[A-Za-z_/][A-Za-z0-9._/:@+=-]*")
_YAML_WORDS = {"y", "n", "yes", "no", "true", "false", "on", "off", "null"}


def _text(value, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ValueError(f"{field} contains a control character")
    return value


def _port(value, field: str = "port") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError(f"{field} must be an integer 1-65535")
    return value


def _yq(value: str) -> str:
    """value as a YAML scalar: bare when unambiguous, else double-quoted."""
    if (_YAML_BARE.fullmatch(value) and not value.endswith(":")
            and value.lower() not in _YAML_WORDS):
        return value
    # JSON's ASCII \uXXXX escapes are YAML-legal in the BMP (C1 controls
    # included), but YAML refuses surrogate escapes: astral chars go out as
    # YAML's own \UXXXXXXXX.
    return '"%s"' % "".join(json.dumps(c)[1:-1] if ord(c) < 0x10000 else "\\U%08X" % ord(c)
                           for c in value)


def _unit_value(value: str) -> str:
    """systemd expands %-specifiers in most settings; %% is a literal %."""
    return value.replace("%", "%%")


def _unit_quoted(value: str) -> str:
    """Body of a "..." systemd word: backslash and quote escaped."""
    return _unit_value(value).replace("\\", "\\\\").replace('"', '\\"')


def _unit_word(value: str) -> str:
    """One word of a whitespace-split systemd setting (ExecStart, ReadWritePaths)."""
    if value and not re.search(r'[\s"\'\\;]', value):
        return _unit_value(value)
    return '"' + _unit_quoted(value) + '"'


# ─── Docker Compose ───────────────────────────────────────────────────

def compose_yaml(
    *,
    include_qbittorrent: bool = True,
    include_flaresolverr: bool = True,
    include_vpn_sidecar: bool = False,
    bd_image: str = "bulkdownloader:latest",
    bd_port: int = 5000,
    install_path: str = "/data/bd",
    downloads_path: str = "/data/downloads",
    timezone: str = "UTC",
) -> str:
    """Render a docker-compose.yml stack.

    bulkdownloader depends_on only the services this stack defines. With
    the VPN sidecar it shares the sidecar's network namespace, so its port
    is published on the sidecar (compose refuses ports + network_mode).
    """
    bd_image = _text(bd_image, "bd_image")
    bd_port = _port(bd_port, "bd_port")
    install_path = _text(install_path, "install_path")
    downloads_path = _text(downloads_path, "downloads_path")
    timezone = _text(timezone, "timezone")
    depends_on = [name for name, on in (("flaresolverr", include_flaresolverr),
                                        ("vpn", include_vpn_sidecar)) if on]
    services = []
    services.append(_bd_service(bd_image, bd_port, install_path,
                               downloads_path, timezone,
                               depends_on=depends_on,
                               behind_vpn=include_vpn_sidecar))
    if include_qbittorrent:
        services.append(_qb_service(downloads_path, timezone))
    if include_flaresolverr:
        services.append(_flaresolverr_service())
    if include_vpn_sidecar:
        services.append(_vpn_service(timezone, bd_port))
    body = "\n".join(services)
    return _compose_header() + body + "\n" + _compose_volumes()


def _compose_header() -> str:
    return (
        "# BulkDownloader stack — generated by edge_deploy.compose_yaml\n"
        "# Customize before applying. Review every line.\n"
        "\n"
        "services:\n"
    )


def _bd_service(image: str, port: int, install: str,
                downloads: str, tz: str, *,
                depends_on=(), behind_vpn: bool = False) -> str:
    if behind_vpn:
        network = '    network_mode: "service:vpn"\n'
    else:
        network = f'    ports:\n      - "{port}:5000"\n'
    needs = "".join(f"      - {name}\n" for name in depends_on)
    if needs:
        needs = "    depends_on:\n" + needs
    return f"""  bulkdownloader:
    image: {_yq(image)}
    container_name: bulkdownloader
    restart: unless-stopped
{network}    volumes:
      - {_yq(install + ":/install")}
      - {_yq(downloads + ":/downloads")}
      - bd-state:/state
    environment:
      - {_yq("TZ=" + tz)}
      - BD_INSTALL_DIR=/install
      - BD_DOWNLOAD_DIR=/downloads
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:5000/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
{needs}"""


def _qb_service(downloads: str, tz: str) -> str:
    return f"""  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    restart: unless-stopped
    ports:
      - "8080:8080"
      - "6881:6881"
      - "6881:6881/udp"
    volumes:
      - qb-config:/config
      - {_yq(downloads + ":/downloads")}
    environment:
      - PUID=1000
      - PGID=1000
      - {_yq("TZ=" + tz)}
      - WEBUI_PORT=8080
"""


def _flaresolverr_service() -> str:
    return """  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    container_name: flaresolverr
    restart: unless-stopped
    ports:
      - "8191:8191"
    environment:
      - LOG_LEVEL=info
      - CAPTCHA_SOLVER=none
"""


def _vpn_service(tz: str, bd_port: int) -> str:
    return f"""  vpn:
    image: qmcgaw/gluetun:latest
    container_name: vpn
    restart: unless-stopped
    ports:
      - "{bd_port}:5000"  # bulkdownloader runs in this namespace
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      - VPN_SERVICE_PROVIDER=mullvad  # change to your provider
      - VPN_TYPE=wireguard
      - {_yq("TZ=" + tz)}
      # Set WIREGUARD_PRIVATE_KEY etc via .env file
    volumes:
      - vpn-config:/gluetun
"""


def _compose_volumes() -> str:
    return """
volumes:
  bd-state:
  qb-config:
  vpn-config:
"""


# ─── systemd unit ─────────────────────────────────────────────────────

def systemd_unit(
    *,
    user: str = "bd",
    install_path: str = "/opt/bulkdownloader",
    python_path: str = "/usr/bin/python3",
    port: int = 5000,
) -> str:
    """Render a systemd .service file for running BD on bare metal."""
    user = _unit_value(_text(user, "user"))
    workdir = _unit_value(_text(install_path, "install_path"))
    env_install = _unit_quoted(install_path)
    rw_install = _unit_word(install_path)
    python_path = _unit_word(_text(python_path, "python_path"))
    port = _port(port)
    return f"""[Unit]
Description=BulkDownloader
Documentation=https://github.com/bulkdownloader
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={user}
WorkingDirectory={workdir}
Environment="BD_INSTALL_DIR={env_install}"
Environment="PYTHONUNBUFFERED=1"
ExecStart={python_path} -m bulk_downloader.app --port {port}
Restart=on-failure
RestartSec=10s
TimeoutStopSec=30s

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={rw_install}
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

# Journald logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=bulkdownloader

[Install]
WantedBy=multi-user.target
"""


# ─── Kubernetes manifest ──────────────────────────────────────────────

def k8s_manifests(
    *,
    namespace: str = "bd",
    image: str = "bulkdownloader:latest",
    replicas: int = 1,
    storage_class: str = "standard",
    state_size_gb: int = 5,
    downloads_size_gb: int = 500,
) -> str:
    """Render Namespace + PVCs + Deployment + Service in one YAML."""
    namespace = _yq(_text(namespace, "namespace"))
    image = _yq(_text(image, "image"))
    storage_class = _yq(_text(storage_class, "storage_class"))
    return f"""---
apiVersion: v1
kind: Namespace
metadata:
  name: {namespace}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: bd-state
  namespace: {namespace}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: {storage_class}
  resources:
    requests:
      storage: {state_size_gb}Gi
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: bd-downloads
  namespace: {namespace}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: {storage_class}
  resources:
    requests:
      storage: {downloads_size_gb}Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bulkdownloader
  namespace: {namespace}
spec:
  replicas: {replicas}
  strategy:
    type: Recreate  # state is per-pod; never run 2 at once
  selector:
    matchLabels:
      app: bulkdownloader
  template:
    metadata:
      labels:
        app: bulkdownloader
    spec:
      containers:
        - name: bulkdownloader
          image: {image}
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 5000
              name: http
          env:
            - name: BD_INSTALL_DIR
              value: /install
            - name: BD_DOWNLOAD_DIR
              value: /downloads
          volumeMounts:
            - name: state
              mountPath: /install
            - name: downloads
              mountPath: /downloads
          readinessProbe:
            httpGet:
              path: /api/health
              port: 5000
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /api/health
              port: 5000
            initialDelaySeconds: 30
            periodSeconds: 30
          resources:
            requests:
              memory: 512Mi
              cpu: 250m
            limits:
              memory: 4Gi
              cpu: 2000m
      volumes:
        - name: state
          persistentVolumeClaim:
            claimName: bd-state
        - name: downloads
          persistentVolumeClaim:
            claimName: bd-downloads
---
apiVersion: v1
kind: Service
metadata:
  name: bulkdownloader
  namespace: {namespace}
spec:
  selector:
    app: bulkdownloader
  ports:
    - port: 5000
      targetPort: 5000
      name: http
  type: ClusterIP
"""


# ─── Bundle endpoint shape (for /api/deploy/* routes) ─────────────────

def all_artifacts(**kwargs) -> dict:
    """Render every artifact in one call. Useful for the
    'download deploy bundle' UI button.

    bd_image / bd_port also set the k8s image and the systemd --port
    unless the caller names image / port for those explicitly."""
    if "bd_image" in kwargs:
        kwargs.setdefault("image", kwargs["bd_image"])
    if "bd_port" in kwargs:
        kwargs.setdefault("port", kwargs["bd_port"])
    return {
        "docker-compose.yml": compose_yaml(**{
            k: v for k, v in kwargs.items()
            if k in ("include_qbittorrent", "include_flaresolverr",
                     "include_vpn_sidecar", "bd_image", "bd_port",
                     "install_path", "downloads_path", "timezone")
        }),
        "bulkdownloader.service": systemd_unit(**{
            k: v for k, v in kwargs.items()
            if k in ("user", "install_path", "python_path", "port")
        }),
        "k8s.yaml": k8s_manifests(**{
            k: v for k, v in kwargs.items()
            if k in ("namespace", "image", "replicas", "storage_class",
                     "state_size_gb", "downloads_size_gb")
        }),
    }
