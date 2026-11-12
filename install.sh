#!/usr/bin/env bash
# ==============================================================================
# GA36 Clone & R36S PC Dashboard - Master All-in-One Installer & Control Suite
# Repository: https://github.com/Auxerov/Dashboard-Image-for-GA36-Clone
# ==============================================================================

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="ga36-dashboard.service"
LEGACY_SERVICE_NAME="r36s-companion.service"
PYTHON_SCRIPT="${REPO_DIR}/ga36_dashboard.py"
UDEV_RULE_FILE="/etc/udev/rules.d/99-ga36-dashboard.rules"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

print_banner() {
    echo -e "${CYAN}${BOLD}"
    echo "============================================================"
    echo "  ⚡ GA36 Clone PC Cyber Dashboard - Master Suite v3.2"
    echo "============================================================"
    echo -e "${NC}"
}

check_root_for_packages() {
    if command -v sudo >/dev/null 2>&1; then
        SUDO_CMD="sudo"
    else
        SUDO_CMD=""
    fi
}

install_dependencies() {
    echo -e "${GREEN}[1/4] Bağımlılıklar denetleniyor (Python3, Serial, Pillow, psutil)...${NC}"
    check_root_for_packages

    if command -v apt-get >/dev/null 2>&1; then
        echo -e "${CYAN}APT paket yöneticisi tespit edildi (Debian/Ubuntu/Mint)...${NC}"
        $SUDO_CMD apt-get update -qq || true
        $SUDO_CMD apt-get install -y python3 python3-serial python3-pil python3-psutil >/dev/null 2>&1 || true
    elif command -v pacman >/dev/null 2>&1; then
        echo -e "${CYAN}Pacman paket yöneticisi tespit edildi (Arch/Manjaro)...${NC}"
        $SUDO_CMD pacman -Sy --noconfirm python python-pyserial python-pillow python-psutil >/dev/null 2>&1 || true
    elif command -v dnf >/dev/null 2>&1; then
        echo -e "${CYAN}DNF paket yöneticisi tespit edildi (Fedora/RHEL)...${NC}"
        $SUDO_CMD dnf install -y python3 python3-pyserial python3-pillow python3-psutil >/dev/null 2>&1 || true
    elif command -v zypper >/dev/null 2>&1; then
        echo -e "${CYAN}Zypper paket yöneticisi tespit edildi (openSUSE)...${NC}"
        $SUDO_CMD zypper in -y python3 python3-pyserial python3-Pillow python3-psutil >/dev/null 2>&1 || true
    fi

    # Fallback to pip if missing
    if ! python3 -c "import serial, PIL, psutil" 2>/dev/null; then
        echo -e "${YELLOW}Bazı Python modülleri eksik, pip ile tamamlanıyor...${NC}"
        python3 -m pip install --user --upgrade pyserial pillow psutil >/dev/null 2>&1 || \
        pip install --user --upgrade pyserial pillow psutil >/dev/null 2>&1 || \
        pip install --break-system-packages --user pyserial pillow psutil >/dev/null 2>&1 || true
    fi

    if python3 -c "import serial, PIL, psutil" 2>/dev/null; then
        echo -e "${GREEN}✓ Gerekli Python kütüphaneleri hazır!${NC}"
    else
        echo -e "${RED}[UYARI] Python kütüphaneleri otomatik yüklenemedi. Manuel yükleyin: pip install pyserial pillow psutil${NC}"
    fi
}

install_udev_rules() {
    echo -e "${GREEN}[2/4] USB Aygıt Erişim İzinleri (udev kuralları) Yapılandırılıyor...${NC}"
    check_root_for_packages

    RULE_CONTENT='# GA36 Clone & R36S CDC-ACM USB Serial Permissions
KERNEL=="ttyACM[0-9]*", MODE="0666", GROUP="dialout"
KERNEL=="ttyGS[0-9]*", MODE="0666", GROUP="dialout"
SUBSYSTEM=="tty", ATTRS{idVendor}=="1d6b", ATTRS{idProduct}=="0104", MODE="0666", GROUP="dialout"
'

    echo "$RULE_CONTENT" | $SUDO_CMD tee "$UDEV_RULE_FILE" >/dev/null
    $SUDO_CMD udevadm control --reload-rules 2>/dev/null || true
    $SUDO_CMD udevadm trigger 2>/dev/null || true

    # Also add user to dialout if needed
    if [ -n "$USER" ]; then
        $SUDO_CMD usermod -aG dialout "$USER" 2>/dev/null || true
    fi
    echo -e "${GREEN}✓ USB udev kuralları yüklendi (/dev/ttyACM* artık şifresiz açılacak).${NC}"
}

install_systemd_service() {
    echo -e "${GREEN}[3/4] PC Başlangıç Servisi (systemd user unit) Kuruluyor...${NC}"
    mkdir -p "$SYSTEMD_USER_DIR"

    SERVICE_FILE="${SYSTEMD_USER_DIR}/${SERVICE_NAME}"
    cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=GA36 Clone & R36S PC Cyber Dashboard Service
After=network.target default.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${PYTHON_SCRIPT} dashboard
Restart=always
RestartSec=3
KillMode=process

[Install]
WantedBy=default.target
EOF

    # Stop any legacy r36s service if present
    systemctl --user stop "$LEGACY_SERVICE_NAME" 2>/dev/null || true
    systemctl --user disable "$LEGACY_SERVICE_NAME" 2>/dev/null || true

    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    systemctl --user restart "$SERVICE_NAME"
    echo -e "${GREEN}✓ Servis etkinleştirildi ve arka planda başlatıldı (${SERVICE_NAME}).${NC}"
}

verify_and_report() {
    echo -e "${GREEN}[4/4] Bağlantı ve Donanım Doğrulaması Yapılıyor...${NC}"
    sleep 1

    PORT=""
    for cand in /dev/ttyACM0 /dev/ttyACM1 /dev/ttyACM2; do
        if [ -e "$cand" ]; then
            PORT="$cand"
            break
        fi
    done

    echo ""
    echo -e "${GREEN}${BOLD}============================================================${NC}"
    echo -e "${GREEN}${BOLD} 🎉 KURULUM BAŞARIYLA TAMAMLANDI!${NC}"
    echo -e "${GREEN}${BOLD}============================================================${NC}"
    if [ -n "$PORT" ]; then
        echo -e " Cihaz Durumu: ${GREEN}BAĞLI (${PORT})${NC}"
        echo -e " Ekran Akışı : ${GREEN}AKTİF (640x480 Giant Pie Cyber HUD)${NC}"
    else
        echo -e " Cihaz Durumu: ${YELLOW}Bekleniyor... (Lütfen GA36'yı USB ile bağlayıp açın)${NC}"
    fi
    echo -e " Başlangıç   : ${GREEN}Bilgisayar her açıldığında otomatik başlar${NC}"
    echo -e "${GREEN}${BOLD}============================================================${NC}"
    echo ""
    echo "Kullanabileceğiniz Komutlar:"
    echo -e "  ${CYAN}./install.sh status${NC}     -> Servis ve bağlantı durumunu gör"
    echo -e "  ${CYAN}./install.sh dashboard${NC}  -> Canlı terminal ekranı ve tema değiştirici"
    echo -e "  ${CYAN}./install.sh stop${NC}       -> Ekran akışını durdur"
    echo -e "  ${CYAN}./install.sh start${NC}      -> Ekran akışını tekrar başlat"
    echo -e "  ${CYAN}./install.sh logs${NC}       -> Canlı sistem loglarını izle"
    echo -e "  ${CYAN}./install.sh image <dosya>${NC} -> Konsol ekranına resim gönder"
    echo -e "  ${CYAN}./install.sh shell${NC}      -> Konsola canlı root terminali aç"
    echo -e "  ${CYAN}./install.sh restore${NC}    -> Konsolu orijinal oyun menüsüne döndür"
    echo ""
}

cmd_start() {
    echo -e "${CYAN}Dashboard servisi başlatılıyor...${NC}"
    systemctl --user start "$SERVICE_NAME"
    echo -e "${GREEN}✓ Başlatıldı.${NC}"
}

cmd_stop() {
    echo -e "${YELLOW}Dashboard servisi durduruluyor...${NC}"
    systemctl --user stop "$SERVICE_NAME"
    systemctl --user stop "$LEGACY_SERVICE_NAME" 2>/dev/null || true
    echo -e "${GREEN}✓ Durduruldu.${NC}"
}

cmd_restart() {
    echo -e "${CYAN}Dashboard servisi yeniden başlatılıyor...${NC}"
    systemctl --user restart "$SERVICE_NAME"
    echo -e "${GREEN}✓ Yeniden başlatıldı.${NC}"
}

cmd_status() {
    print_banner
    systemctl --user status "$SERVICE_NAME" --no-pager
    echo ""
    if ls /dev/ttyACM* >/dev/null 2>&1; then
        echo -e "Tespit edilen USB Portları: ${GREEN}$(ls /dev/ttyACM*)${NC}"
    else
        echo -e "${YELLOW}USB portunda aktif cihaz görünmüyor (USB kablosunu takın).${NC}"
    fi
}

cmd_logs() {
    journalctl --user -u "$SERVICE_NAME" -f
}

cmd_dashboard() {
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    python3 "$PYTHON_SCRIPT" dashboard
}

cmd_image() {
    if [ -z "$1" ]; then
        echo -e "${RED}[HATA] Resim dosyası belirtilmedi! Kullanım: ./install.sh image /resim/yolu.png${NC}"
        exit 1
    fi
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    python3 "$PYTHON_SCRIPT" image "$1"
    systemctl --user start "$SERVICE_NAME" 2>/dev/null || true
}

cmd_battery() {
    python3 "$PYTHON_SCRIPT" battery
}

cmd_shell() {
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    python3 "$PYTHON_SCRIPT" shell
    systemctl --user start "$SERVICE_NAME" 2>/dev/null || true
}

cmd_mode() {
    python3 "$PYTHON_SCRIPT" mode "$@"
}

cmd_restore() {
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    python3 "$PYTHON_SCRIPT" restore
}

cmd_uninstall() {
    print_banner
    echo -e "${YELLOW}GA36 Dashboard PC bileşenleri kaldırılıyor...${NC}"
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl --user disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "${SYSTEMD_USER_DIR}/${SERVICE_NAME}"
    systemctl --user daemon-reload

    check_root_for_packages
    if [ -f "$UDEV_RULE_FILE" ]; then
        $SUDO_CMD rm -f "$UDEV_RULE_FILE"
        $SUDO_CMD udevadm control --reload-rules 2>/dev/null || true
    fi
    echo -e "${GREEN}✓ Servis ve udev kuralları temizlendi.${NC}"
}

# --- Main Dispatcher ---
ACTION="${1:-install}"
shift || true

case "$ACTION" in
    install|"")
        print_banner
        install_dependencies
        install_udev_rules
        install_systemd_service
        verify_and_report
        ;;
    start)
        cmd_start
        ;;
    stop)
        cmd_stop
        ;;
    restart)
        cmd_restart
        ;;
    status)
        cmd_status
        ;;
    logs|log)
        cmd_logs
        ;;
    dashboard|dash|live)
        cmd_dashboard
        ;;
    image|img)
        cmd_image "$@"
        ;;
    battery|pwr)
        cmd_battery
        ;;
    shell|term)
        cmd_shell
        ;;
    restore)
        cmd_restore
        ;;
    mode|theme)
        cmd_mode "$@"
        ;;
    backup)
        sudo "${REPO_DIR}/scripts/backup_image.sh" "$@"
        ;;
    uninstall|remove)
        cmd_uninstall
        ;;
    help|--help|-h)
        print_banner
        echo "Kullanım: ./install.sh [KOMUT]"
        echo ""
        echo "Komutlar:"
        echo "  install        -> Tam otomatik kurulum (Varsayılan)"
        echo "  start          -> Ekran servisini arka planda başlat"
        echo "  stop           -> Ekran servisini durdur"
        echo "  restart        -> Servisi yeniden başlat"
        echo "  status         -> Durum ve USB bağlantısını kontrol et"
        echo "  logs           -> Canlı sistem loglarını incele"
        echo "  dashboard      -> Canlı terminal göstergesini aç"
        echo "  mode [tema]    -> HUD temasını değiştir (pie, dual, btop veya sıradaki)"
        echo "  image <dosya>  -> Konsola resim / duvar kağıdı gönder"
        echo "  battery        -> Şarj ve güç detaylarını göster"
        echo "  shell          -> Konsola tam yetkili root terminali aç"
        echo "  restore        -> Orijinal konsol oyun menüsüne dön"
        echo "  backup         -> SD kartın tam .img.xz yedeğini al"
        echo "  uninstall      -> Servisi ve ayarları kaldır"
        ;;
    *)
        echo -e "${RED}[HATA] Bilinmeyen komut: '$ACTION'${NC}"
        echo "Kullanım için: ./install.sh --help"
        exit 1
        ;;
esac
