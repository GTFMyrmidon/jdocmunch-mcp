#!/bin/bash
# Build a Debian/Ubuntu .deb package for jdocmunch-mcp.
# Contributed by @Tikilou (GitHub issue #7) — originally written for Proxmox containers.
#
# Usage:
#   cd /path/to/jdocmunch-mcp
#   bash contrib/build-deb.sh
#
# Prerequisites: dpkg-deb, python3, python3-venv, pip

set -e

# --- Package Configuration ---
PACKAGE_NAME="jdocmunch-mcp"
SERVICE_USER="jdocmunch"
MAINTAINER="Tikilou <tikilou@local>"
DESCRIPTION="Optimized MCP server for documentation exploration (Debian standard)"
BUILD_DIR="debian_build"
INSTALL_PATH="/opt/$PACKAGE_NAME"
DATA_PATH="/var/lib/$PACKAGE_NAME"

# 1. Check build prerequisites
for cmd in dpkg-deb python3 pip; do
    if ! command -v $cmd &> /dev/null; then
        echo "Error: Build tools (dpkg-dev, python3-venv) are required."
        exit 1
    fi
done

if ! python3 -m venv --help &> /dev/null; then
    echo "Error: python3-venv is required."
    exit 1
fi

# 2. Extract version
VERSION=$(grep -m 1 "version =" pyproject.toml | cut -d '"' -f 2)
if [ -z "$VERSION" ]; then VERSION="1.0.0"; fi
echo "Building package $PACKAGE_NAME v$VERSION for Debian 13..."

# 3. Prepare directory tree
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/DEBIAN"
mkdir -p "$BUILD_DIR$INSTALL_PATH"
mkdir -p "$BUILD_DIR/usr/bin"
mkdir -p "$BUILD_DIR$DATA_PATH"
mkdir -p "$BUILD_DIR/lib/systemd/system"

# 4. Create VirtualEnv (dependency isolation)
echo "Creating virtual environment in $INSTALL_PATH..."
python3 -m venv "$BUILD_DIR$INSTALL_PATH"
"$BUILD_DIR$INSTALL_PATH/bin/pip" install --upgrade pip
# Add uvicorn, starlette and sse-starlette, which are not included by default in this project
"$BUILD_DIR$INSTALL_PATH/bin/pip" install ".[anthropic,gemini,openai]" uvicorn starlette sse-starlette anyio

# Replace absolute build paths with the final install path in venv scripts
BUILD_ABS_PATH=$(realpath "$BUILD_DIR$INSTALL_PATH")
find "$BUILD_DIR$INSTALL_PATH/bin" -type f -exec sed -i "s|$BUILD_ABS_PATH|/opt/$PACKAGE_NAME|g" {} +

# 5. Streamable HTTP network wrapper for jdocmunch-mcp (modern MCP protocol)
cat <<EOF > "$BUILD_DIR$INSTALL_PATH/bin/jdocmunch-mcp-http"
#!/opt/$PACKAGE_NAME/bin/python3
import sys
import os
import contextlib
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from jdocmunch_mcp.server import server

port = int(os.environ.get("PORT", 8902))
host = os.environ.get("HOST", "0.0.0.0")

session_manager = StreamableHTTPSessionManager(
    app=server,
    stateless=True,
    json_response=True,
)

@contextlib.asynccontextmanager
async def lifespan(app):
    async with session_manager.run():
        print(f"jDocMunch-MCP Streamable HTTP server at http://{host}:{port}/mcp", file=sys.stderr)
        yield

app = Starlette(
    routes=[
        Mount("/mcp", app=session_manager.handle_request),
    ],
    middleware=[
        Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
EOF
chmod +x "$BUILD_DIR$INSTALL_PATH/bin/jdocmunch-mcp-http"

# 6. System executable wrapper
cat <<EOF > "$BUILD_DIR/usr/bin/$PACKAGE_NAME"
#!/bin/bash
# Define the storage path for index databases
export DOC_INDEX_PATH="$DATA_PATH"
exec "$INSTALL_PATH/bin/jdocmunch-mcp-http" "\$@"
EOF
chmod +x "$BUILD_DIR/usr/bin/$PACKAGE_NAME"

# 7. Systemd unit
cat <<EOF > "$BUILD_DIR/lib/systemd/system/$PACKAGE_NAME.service"
[Unit]
Description=jDocMunch MCP Server (Documentation Indexer)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
Environment=DOC_INDEX_PATH=$DATA_PATH
Environment=PORT=8902
Environment=HOST=0.0.0.0
# Start with the Streamable HTTP bridge (modern MCP protocol)
ExecStart=/usr/bin/$PACKAGE_NAME
Restart=always
RestartSec=5
# Security: filesystem restrictions
ReadWritePaths=$DATA_PATH
CapabilityBoundingSet=
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

# 8. Debian scripts (Post-inst / Prerm)
cat <<EOF > "$BUILD_DIR/DEBIAN/postinst"
#!/bin/bash
set -e
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir $DATA_PATH --shell /usr/sbin/nologin $SERVICE_USER
fi

mkdir -p $DATA_PATH
chown -R $SERVICE_USER:$SERVICE_USER $DATA_PATH
chmod 750 $DATA_PATH

if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
fi

echo "Installation completed."
echo "Enable the service with: sudo systemctl enable --now $PACKAGE_NAME"
EOF

cat <<EOF > "$BUILD_DIR/DEBIAN/prerm"
#!/bin/bash
set -e
if [ "\$1" = "remove" ] || [ "\$1" = "upgrade" ]; then
    if systemctl is-active --quiet $PACKAGE_NAME; then
        systemctl stop $PACKAGE_NAME || true
    fi
fi
EOF
chmod 755 "$BUILD_DIR/DEBIAN/postinst" "$BUILD_DIR/DEBIAN/prerm"

# 9. Control file
cat <<EOF > "$BUILD_DIR/DEBIAN/control"
Package: $PACKAGE_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Maintainer: $MAINTAINER
Depends: python3, python3-venv
Description: $DESCRIPTION
 An MCP server for segmented documentation exploration.
 Installed in a standardized way for Debian 13 (Bookworm+).
 Isolated in $INSTALL_PATH.
EOF

# 10. Final build
dpkg-deb --build "$BUILD_DIR" "${PACKAGE_NAME}_${VERSION}_all.deb"
rm -rf "$BUILD_DIR"

echo "------------------------------------------------------"
echo "SUCCESS: ${PACKAGE_NAME}_${VERSION}_all.deb generated."
echo "To install it: sudo apt install ./${PACKAGE_NAME}_${VERSION}_all.deb"
echo "------------------------------------------------------"
