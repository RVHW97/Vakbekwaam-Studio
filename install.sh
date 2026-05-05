#!/usr/bin/env bash
# ==============================================================================
#  Vakbekwaam Studio — one-line installer voor Ubuntu VPS
# ==============================================================================
#  Gebruik:
#     curl -fsSL https://raw.githubusercontent.com/RVHW97/Vakbekwaam-Studio/main/install.sh | sudo bash
#
#  Wat dit script doet:
#   1) Vraagt om FQDN, admin-mail en SSL (Let's Encrypt) ja/nee
#   2) Installeert Docker + Docker Compose plugin
#   3) Cloned de repo naar /opt/vakbekwaam-studio
#   4) Genereert een .env met sterke SECRET_KEY + admin-wachtwoord
#   5) Start app + nginx via Docker Compose
#   6) Vraagt (indien gekozen) een Let's Encrypt certificaat aan en zet HTTPS aan
#   7) Print de admin-inloggegevens en de locatie van de install-log
# ==============================================================================

set -euo pipefail

# --- Vaste paden ---
INSTALL_DIR="/opt/vakbekwaam-studio"
REPO_URL="https://github.com/RVHW97/Vakbekwaam-Studio.git"
LOG_FILE="/var/log/vakbekwaam-install-$(date +%Y%m%d-%H%M%S).log"

# --- Output naar terminal én logfile ---
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee "$LOG_FILE") 2>&1
chmod 600 "$LOG_FILE" || true

# --- Kleuren ---
C_RED=$'\033[0;31m'
C_GRN=$'\033[0;32m'
C_YLW=$'\033[1;33m'
C_BLU=$'\033[0;34m'
C_BLD=$'\033[1m'
C_RST=$'\033[0m'

info()  { echo "${C_BLU}→${C_RST} $*"; }
ok()    { echo "${C_GRN}✓${C_RST} $*"; }
warn()  { echo "${C_YLW}⚠${C_RST} $*"; }
err()   { echo "${C_RED}✗${C_RST} $*" >&2; }

# --- Banner ---
cat <<'BANNER'

╔══════════════════════════════════════════════════════════════╗
║          Vakbekwaam Studio — Installer (Ubuntu)              ║
║          Brandweer Limburg-Noord                             ║
╚══════════════════════════════════════════════════════════════╝

BANNER

# --- Pre-flight checks -------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
    err "Dit script moet als root draaien. Probeer:"
    err "    curl -fsSL https://raw.githubusercontent.com/RVHW97/Vakbekwaam-Studio/main/install.sh | sudo bash"
    exit 1
fi

if [[ ! -f /etc/os-release ]] || ! grep -qi 'ID=ubuntu' /etc/os-release; then
    err "Dit script is geschreven voor Ubuntu. Andere distro's zijn niet getest."
    exit 1
fi

if [[ -f "$INSTALL_DIR/.env" ]]; then
    err "Vakbekwaam Studio is al geïnstalleerd in $INSTALL_DIR."
    err "Voor een herinstallatie:  rm -rf $INSTALL_DIR  en draai dit script opnieuw."
    err "Voor een update:          cd $INSTALL_DIR && git pull && docker compose up -d --build"
    exit 1
fi

# --- Vragen aan de gebruiker (lezen uit /dev/tty want stdin = curl-pipe) -----

echo "${C_BLD}Stap 1 — Configuratie${C_RST}"
echo

# FQDN
while true; do
    read -rp "Domeinnaam (FQDN, bv. vakbekwaam.brandweerln.nl): " FQDN < /dev/tty
    if [[ "$FQDN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+$ ]]; then
        break
    fi
    warn "Ongeldig domein. Voorbeeld: vakbekwaam.brandweerln.nl"
done

# Admin e-mail
while true; do
    read -rp "Admin e-mailadres (login voor de app): " ADMIN_EMAIL < /dev/tty
    if [[ "$ADMIN_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
        break
    fi
    warn "Ongeldig e-mailadres."
done

# SSL ja/nee
read -rp "SSL via Let's Encrypt aanzetten? [J/n]: " SSL_INPUT < /dev/tty
SSL_INPUT="${SSL_INPUT:-J}"
if [[ "$SSL_INPUT" =~ ^[Jj]$ ]]; then
    USE_SSL="ja"
else
    USE_SSL="nee"
fi

# Let's Encrypt e-mail (alleen als SSL aan staat)
LE_EMAIL="$ADMIN_EMAIL"
if [[ "$USE_SSL" == "ja" ]]; then
    read -rp "Contact-mail voor Let's Encrypt [$ADMIN_EMAIL]: " LE_INPUT < /dev/tty
    LE_EMAIL="${LE_INPUT:-$ADMIN_EMAIL}"
    echo
    warn "Zorg dat de DNS A-record van $FQDN al naar deze server wijst."
    warn "Anders mislukt de certificaat-aanvraag."
    echo
    read -rp "DNS staat goed en doorgaan? [J/n]: " DNS_OK < /dev/tty
    DNS_OK="${DNS_OK:-J}"
    if [[ ! "$DNS_OK" =~ ^[Jj]$ ]]; then
        err "Afgebroken. Wijzig eerst je DNS en draai het script opnieuw."
        exit 1
    fi
fi

# --- Sterke geheimen genereren ----------------------------------------------

SECRET_KEY="$(openssl rand -hex 32)"
ADMIN_WACHTWOORD="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"

# --- Docker installeren indien nodig ----------------------------------------

echo
echo "${C_BLD}Stap 2 — Docker installeren${C_RST}"

if ! command -v docker &>/dev/null; then
    info "Docker niet gevonden — installeren via get.docker.com..."
    curl -fsSL https://get.docker.com | sh
    ok "Docker geïnstalleerd"
else
    ok "Docker al aanwezig: $(docker --version)"
fi

if ! docker compose version &>/dev/null; then
    info "Docker Compose plugin installeren..."
    apt-get update -qq
    apt-get install -y -qq docker-compose-plugin
    ok "Docker Compose plugin geïnstalleerd"
else
    ok "Docker Compose al aanwezig: $(docker compose version | head -1)"
fi

systemctl enable --now docker >/dev/null 2>&1 || true

# --- Repo clonen -------------------------------------------------------------

echo
echo "${C_BLD}Stap 3 — Code ophalen${C_RST}"

if [[ ! -d "$INSTALL_DIR" ]]; then
    info "Repo clonen naar $INSTALL_DIR..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    ok "Code gedownload"
else
    info "$INSTALL_DIR bestaat al — laatste versie ophalen..."
    git -C "$INSTALL_DIR" pull --ff-only
fi

cd "$INSTALL_DIR"

# Data-dirs aanmaken
mkdir -p data/db data/uploads data/certbot/certs data/certbot/www
chmod 700 data

# --- .env-bestand schrijven --------------------------------------------------

echo
echo "${C_BLD}Stap 4 — Configuratiebestand schrijven${C_RST}"

cat > .env <<ENV
# Automatisch gegenereerd door install.sh op $(date '+%Y-%m-%d %H:%M:%S')
SECRET_KEY=$SECRET_KEY
ADMIN_EMAIL=$ADMIN_EMAIL
ADMIN_WACHTWOORD=$ADMIN_WACHTWOORD

# Installatie-info (gebruikt door update.sh — niet door de Flask-app)
INSTALL_FQDN=$FQDN
INSTALL_SSL=$USE_SSL
INSTALL_LE_EMAIL=$LE_EMAIL
ENV
chmod 600 .env
ok ".env aangemaakt (rw alleen voor root)"

# --- Nginx-config (HTTP-only voor eerste start + ACME-challenge) -------------

echo
echo "${C_BLD}Stap 5 — Nginx configureren${C_RST}"

mkdir -p docker/nginx/conf.d
sed "s/__FQDN__/$FQDN/g" docker/nginx/templates/http-only.conf.template \
    > docker/nginx/conf.d/default.conf
ok "Nginx HTTP-config gegenereerd voor $FQDN"

# --- App + nginx starten -----------------------------------------------------

echo
echo "${C_BLD}Stap 6 — Containers bouwen en starten${C_RST}"
info "Dit kan 2–4 minuten duren bij eerste keer..."

docker compose up -d --build

info "Wachten tot de app gestart is..."
for i in {1..30}; do
    if docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050', timeout=2)" 2>/dev/null; then
        ok "App reageert"
        break
    fi
    sleep 2
done

# --- SSL via Let's Encrypt ---------------------------------------------------

if [[ "$USE_SSL" == "ja" ]]; then
    echo
    echo "${C_BLD}Stap 7 — SSL-certificaat aanvragen${C_RST}"

    docker compose run --rm --entrypoint "" certbot \
        certbot certonly --webroot \
        -w /var/www/certbot \
        -d "$FQDN" \
        --email "$LE_EMAIL" \
        --agree-tos \
        --no-eff-email \
        --non-interactive

    if [[ -f "data/certbot/certs/live/$FQDN/fullchain.pem" ]]; then
        ok "Certificaat ontvangen voor $FQDN"

        info "Nginx omschakelen naar HTTPS..."
        sed "s/__FQDN__/$FQDN/g" docker/nginx/templates/https.conf.template \
            > docker/nginx/conf.d/default.conf
        docker compose restart nginx
        ok "HTTPS actief"

        APP_URL="https://$FQDN"
    else
        warn "Certificaat-aanvraag is mislukt. App draait wel, maar alleen via HTTP."
        warn "Check de log hierboven, fix het probleem en draai daarna:"
        warn "    cd $INSTALL_DIR && docker compose run --rm certbot certbot certonly --webroot -w /var/www/certbot -d $FQDN --email $LE_EMAIL --agree-tos"
        APP_URL="http://$FQDN"
    fi
else
    APP_URL="http://$FQDN"
fi

# --- Slot: gegevens tonen ----------------------------------------------------

echo
echo "${C_GRN}╔══════════════════════════════════════════════════════════════╗${C_RST}"
echo "${C_GRN}║  ✓ INSTALLATIE VOLTOOID                                      ║${C_RST}"
echo "${C_GRN}╚══════════════════════════════════════════════════════════════╝${C_RST}"
echo
echo "  ${C_BLD}URL${C_RST}              $APP_URL"
echo "  ${C_BLD}Admin e-mail${C_RST}     $ADMIN_EMAIL"
echo "  ${C_BLD}Admin wachtwoord${C_RST} $ADMIN_WACHTWOORD"
echo
echo "  ${C_BLD}Installatiemap${C_RST}   $INSTALL_DIR"
echo "  ${C_BLD}Data + DB${C_RST}        $INSTALL_DIR/data/"
echo
echo "${C_YLW}═══════════════════════════════════════════════════════════════${C_RST}"
echo "${C_YLW}  ⚠  BEWAAR DEZE INLOGGEGEVENS NU OP EEN VEILIGE PLEK${C_RST}"
echo "${C_YLW}     (1Password, Bitwarden, Keeper, of een password manager)${C_RST}"
echo
echo "${C_YLW}     Log direct in en wijzig je wachtwoord via 'Mijn account'.${C_RST}"
echo "${C_YLW}═══════════════════════════════════════════════════════════════${C_RST}"
echo
echo "${C_RED}  📄 Het volledige install-log (incl. wachtwoord) staat op:${C_RST}"
echo "${C_RED}        $LOG_FILE${C_RST}"
echo
echo "${C_RED}     ➜ VERWIJDER deze logfile zodra je de gegevens veilig hebt opgeslagen:${C_RST}"
echo "${C_RED}        sudo rm $LOG_FILE${C_RST}"
echo
echo "  Handige commando's:"
echo "     Status:     cd $INSTALL_DIR && docker compose ps"
echo "     Logs:       cd $INSTALL_DIR && docker compose logs -f"
echo "     Update:     curl -fsSL https://raw.githubusercontent.com/RVHW97/Vakbekwaam-Studio/main/update.sh | sudo bash"
echo "     Stop:       cd $INSTALL_DIR && docker compose down"
echo
