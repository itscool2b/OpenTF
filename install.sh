#!/usr/bin/env bash
set -euo pipefail

OPENTF_HOME="${HOME}/.opentf"
OPENTF_VENV="${OPENTF_HOME}/.venv"
BIN_DIR="${HOME}/.local/bin"
MIN_PYTHON_VERSION="3.12"

info() { printf "\033[1;34m[opentf]\033[0m %s\n" "$1"; }
error() { printf "\033[1;31m[opentf]\033[0m %s\n" "$1" >&2; }
success() { printf "\033[1;32m[opentf]\033[0m %s\n" "$1"; }

# Find Python 3.12+
find_python() {
    for cmd in python3.12 python3.13 python3.14 python3; do
        if command -v "$cmd" &>/dev/null; then
            local version
            version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
            if "$cmd" -c "import sys; exit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

info "Installing OpenTF (Open Task Force)..."

# Check Python
PYTHON=$(find_python) || {
    error "Python ${MIN_PYTHON_VERSION}+ is required but not found."
    error "Install it from https://python.org or via your package manager."
    exit 1
}
info "Found Python: $($PYTHON --version)"

# Create home directory
mkdir -p "${OPENTF_HOME}"

# Create virtual environment
if [ ! -d "${OPENTF_VENV}" ]; then
    info "Creating virtual environment..."
    "$PYTHON" -m venv "${OPENTF_VENV}"
fi

# Install opentf into the venv
info "Installing opentf..."
"${OPENTF_VENV}/bin/pip" install --quiet --upgrade pip
"${OPENTF_VENV}/bin/pip" install --quiet opentf

# Create wrapper script
mkdir -p "${BIN_DIR}"
cat > "${BIN_DIR}/opentf" << 'WRAPPER'
#!/usr/bin/env bash
exec "${HOME}/.opentf/.venv/bin/opentf" "$@"
WRAPPER
chmod +x "${BIN_DIR}/opentf"

# Check if BIN_DIR is in PATH
if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
    info "Adding ${BIN_DIR} to PATH..."
    for rc in "${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.config/fish/config.fish"; do
        if [ -f "$rc" ]; then
            if [[ "$rc" == *"fish"* ]]; then
                echo "fish_add_path ${BIN_DIR}" >> "$rc"
            else
                echo "export PATH=\"${BIN_DIR}:\$PATH\"" >> "$rc"
            fi
        fi
    done
    export PATH="${BIN_DIR}:$PATH"
fi

success "OpenTF installed successfully!"
info "Run 'opentf' to get started."
