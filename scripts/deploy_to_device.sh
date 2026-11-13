#!/usr/bin/env bash
# ==============================================================================
# GA36 Clone / R36S Direct USB Deployer
# Injects the Dashboard agent & boot hooks into a USB-connected console
# ==============================================================================

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${CYAN}${BOLD}"
echo "============================================================"
echo " GA36 Clone Direct USB Mod Deployer"
echo "============================================================"
echo -e "${NC}"

echo -e "${GREEN}[1/3] Scanning for connected console...${NC}"
python3 "${REPO_DIR}/scripts/deploy_worker.py" "${REPO_DIR}"

echo -e "${GREEN}${BOLD}Console is ready! You can now start with './install.sh start'.${NC}"
