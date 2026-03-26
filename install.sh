#!/usr/bin/env bash
set -euo pipefail

OPENTF_HOME="${HOME}/.opentf"
OPENTF_VENV="${OPENTF_HOME}/.venv"
BIN_DIR="${HOME}/.local/bin"
MIN_PYTHON_VERSION="3.12"
UPGRADE=false
FROM_SOURCE=false

info()    { printf "\033[1;34m[opentf]\033[0m %s\n" "$1"; }
error()   { printf "\033[1;31m[opentf]\033[0m %s\n" "$1" >&2; }
success() { printf "\033[1;32m[opentf]\033[0m %s\n" "$1"; }

usage() {
    echo "Usage: install.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --upgrade, -u     Upgrade existing installation"
    echo "  --from-source     Install from current directory (for development)"
    echo "  --help, -h        Show this help"
    exit 0
}

# Parse flags
for arg in "$@"; do
    case "$arg" in
        --upgrade|-u)  UPGRADE=true ;;
        --from-source) FROM_SOURCE=true ;;
        --help|-h)     usage ;;
    esac
done

# Find Python 3.12+
find_python() {
    for cmd in python3.12 python3.13 python3.14 python3; do
        if command -v "$cmd" &>/dev/null; then
            if "$cmd" -c "import sys; exit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

# Detect existing install
if [ -f "${OPENTF_VENV}/bin/opentf" ] && [ "$UPGRADE" = false ] && [ "$FROM_SOURCE" = false ]; then
    info "Existing installation found. Upgrading..."
    UPGRADE=true
fi

if [ "$UPGRADE" = true ]; then
    info "Upgrading OpenTF..."
else
    info "Installing OpenTF..."
fi

# Check Python
PYTHON=$(find_python) || {
    error "Python ${MIN_PYTHON_VERSION}+ is required but not found."
    error "Install from https://python.org or your package manager."
    exit 1
}
info "Found Python: $($PYTHON --version)"

# Create home directory
mkdir -p "${OPENTF_HOME}"

# Create or verify virtual environment
if [ -d "${OPENTF_VENV}" ]; then
    # Verify the venv is healthy
    if ! "${OPENTF_VENV}/bin/python" --version &>/dev/null; then
        info "Existing venv is broken, recreating..."
        rm -rf "${OPENTF_VENV}"
        "$PYTHON" -m venv "${OPENTF_VENV}"
    fi
else
    info "Creating virtual environment..."
    "$PYTHON" -m venv "${OPENTF_VENV}"
fi

# Install opentf
info "Installing packages (this may take a few minutes)..."
"${OPENTF_VENV}/bin/pip" install --quiet --upgrade pip

if [ "$FROM_SOURCE" = true ]; then
    "${OPENTF_VENV}/bin/pip" install --quiet .
elif [ "$UPGRADE" = true ]; then
    "${OPENTF_VENV}/bin/pip" install --quiet --upgrade opentf
else
    "${OPENTF_VENV}/bin/pip" install --quiet opentf
fi

# Create wrapper script
mkdir -p "${BIN_DIR}"
cat > "${BIN_DIR}/opentf" << 'WRAPPER'
#!/usr/bin/env bash
exec "${HOME}/.opentf/.venv/bin/opentf" "$@"
WRAPPER
chmod +x "${BIN_DIR}/opentf"

# Add BIN_DIR to PATH if not already there
if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
    info "Adding ${BIN_DIR} to PATH..."

    # bash
    if [ -f "${HOME}/.bashrc" ]; then
        if ! grep -q "${BIN_DIR}" "${HOME}/.bashrc" 2>/dev/null; then
            echo "export PATH=\"${BIN_DIR}:\$PATH\"" >> "${HOME}/.bashrc"
        fi
    fi

    # zsh
    if [ -f "${HOME}/.zshrc" ]; then
        if ! grep -q "${BIN_DIR}" "${HOME}/.zshrc" 2>/dev/null; then
            echo "export PATH=\"${BIN_DIR}:\$PATH\"" >> "${HOME}/.zshrc"
        fi
    fi

    # fish
    if [ -f "${HOME}/.config/fish/config.fish" ]; then
        if ! grep -q "local/bin" "${HOME}/.config/fish/config.fish" 2>/dev/null; then
            echo "fish_add_path ${BIN_DIR}" >> "${HOME}/.config/fish/config.fish"
        fi
    fi

    export PATH="${BIN_DIR}:$PATH"
fi

echo ""
success "OpenTF installed successfully!"
echo ""
info "Get started:"
echo "  opentf             Launch OpenTF"
echo "  opentf --repl      REPL mode"
echo ""
info "Set your API key (pick one):"
echo "  export ANTHROPIC_API_KEY=\"sk-ant-...\""
echo "  opentf   (will prompt on first run)"
echo ""
