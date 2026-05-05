#!/usr/bin/env bash
# ==============================================================================
#  Vakbekwaam Studio — update script voor een bestaande installatie
# ==============================================================================
#  Gebruik:
#     curl -fsSL https://raw.githubusercontent.com/RVHW97/Vakbekwaam-Studio/main/update.sh | sudo bash
#
#  Wat dit script doet:
#   1) Stopt de app-container en maakt een backup van de SQLite-database
#   2) Pulled de laatste code van GitHub (main-branch)
#   3) Hergenereert de nginx-config uit de templates (FQDN/SSL uit .env)
#   4) Herbouwt de Docker-image en start alle services
#   5) Toont versie-info en houdt alleen de laatste 10 DB-backups
# ==============================================================================

set -euo pipefail

INSTALL_DIR="/opt/vakbekwaam-studio"
LOG_FILE="/var/log/vakbekwaam-update-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee "$LOG_FILE") 2>&1
chmod 600 "$LOG_FILE" || true

# Kleuren
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

cat <<'BANNER'

╔══════════════════════════════════════════════════════════════╗
║          Vakbekwaam Studio — Update                          ║
╚══════════════════════════════════════════════════════════════╝

BANNER

# --- Pre-flight --------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
    err "Dit script moet als root draaien."
    err "Probeer: curl -fsSL https://raw.githubusercontent.com/RVHW97/Vakbekwaam-Studio/main/update.sh | sudo bash"
    exit 1
fi

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    err "Geen Vakbekwaam Studio installatie gevonden in $INSTALL_DIR."
    err "Eerste install? Gebruik dit:"
    err "    curl -fsSL https://raw.githubusercontent.com/RVHW97/Vakbekwaam-Studio/main/install.sh | sudo bash"
    exit 1
fi

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    err ".env-bestand ontbreekt in $INSTALL_DIR — installatie lijkt onvolledig."
    exit 1
fi

cd "$INSTALL_DIR"

# Laad install-info uit .env (FQDN, SSL-keuze) — voor nginx-regeneratie.
# (Niet gevoelige sectie, dus source is OK.)
set -a
# shellcheck disable=SC1091
source .env
set +a

# --- Stap 1: Huidige versie ---------------------------------------------------

echo "${C_BLD}Stap 1 — Huidige versie${C_RST}"
HUIDIG_HASH="$(git rev-parse --short HEAD)"
HUIDIG_BERICHT="$(git log -1 --format='%s' HEAD)"
info "Nu geïnstalleerd: $HUIDIG_HASH — $HUIDIG_BERICHT"

# --- Stap 2: Code ophalen ----------------------------------------------------

echo
echo "${C_BLD}Stap 2 — Nieuwe code ophalen${C_RST}"
git fetch --quiet origin main
NIEUW_HASH="$(git rev-parse --short origin/main)"

if [[ "$HUIDIG_HASH" == "$NIEUW_HASH" ]]; then
    ok "Je draait al de laatste versie ($HUIDIG_HASH). Niets te doen."
    echo
    echo "  Log:  $LOG_FILE  (mag je verwijderen — bevat geen wachtwoorden)"
    exit 0
fi

info "Nieuwe versie beschikbaar: $NIEUW_HASH"
echo
echo "Wijzigingen sinds jouw versie:"
git log --oneline "$HUIDIG_HASH..origin/main" | sed 's/^/    /'
echo

# --- Stap 3: DB-backup ------------------------------------------------------

echo "${C_BLD}Stap 3 — Database-backup${C_RST}"

DB_BESTAND="data/db/vakbekwaam.db"
BACKUP_DIR="data/backups"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

if [[ -f "$DB_BESTAND" ]]; then
    info "App stoppen voor consistente backup..."
    docker compose stop app >/dev/null 2>&1 || true

    BACKUP_BESTAND="$BACKUP_DIR/vakbekwaam-$(date +%Y%m%d-%H%M%S).db"
    cp -p "$DB_BESTAND" "$BACKUP_BESTAND"
    ok "Backup opgeslagen: $BACKUP_BESTAND ($(du -h "$BACKUP_BESTAND" | cut -f1))"

    # Bewaar laatste 10 backups
    BACKUPS_TE_VEEL=$(ls -1t "$BACKUP_DIR"/vakbekwaam-*.db 2>/dev/null | tail -n +11 || true)
    if [[ -n "$BACKUPS_TE_VEEL" ]]; then
        echo "$BACKUPS_TE_VEEL" | xargs rm -f
        info "Oudere backups opgeruimd (laatste 10 bewaard)"
    fi
else
    warn "Database-bestand niet gevonden — backup overgeslagen."
fi

# --- Stap 4: Code updaten ---------------------------------------------------

echo
echo "${C_BLD}Stap 4 — Code updaten${C_RST}"
git pull --ff-only origin main
ok "Code op versie $NIEUW_HASH"

# --- Stap 5: Nginx-config hergeneren ----------------------------------------

echo
echo "${C_BLD}Stap 5 — Nginx-config bijwerken${C_RST}"

if [[ -z "${INSTALL_FQDN:-}" ]]; then
    warn "INSTALL_FQDN ontbreekt in .env — config niet hergegenereerd."
    warn "Voeg deze toe aan .env als je nginx-templates hebt zien wijzigen:"
    warn "    INSTALL_FQDN=jouw.domein.nl"
    warn "    INSTALL_SSL=ja|nee"
else
    mkdir -p docker/nginx/conf.d
    if [[ "${INSTALL_SSL:-nee}" == "ja" && -f "data/certbot/certs/live/$INSTALL_FQDN/fullchain.pem" ]]; then
        sed "s/__FQDN__/$INSTALL_FQDN/g" docker/nginx/templates/https.conf.template \
            > docker/nginx/conf.d/default.conf
        ok "Nginx HTTPS-config bijgewerkt voor $INSTALL_FQDN"
    else
        sed "s/__FQDN__/$INSTALL_FQDN/g" docker/nginx/templates/http-only.conf.template \
            > docker/nginx/conf.d/default.conf
        ok "Nginx HTTP-config bijgewerkt voor $INSTALL_FQDN"
    fi
fi

# --- Stap 6: Containers herbouwen + starten ---------------------------------

echo
echo "${C_BLD}Stap 6 — Containers herbouwen en starten${C_RST}"
info "Image opnieuw bouwen (kan 1–3 minuten duren)..."

docker compose up -d --build

info "Wachten tot de app weer reageert (max 60 sec)..."
# `< /dev/null` voorkomt dat docker exec de rest van dit script consumeert
# wanneer update.sh via `curl | bash` wordt gedraaid.
APP_OK="nee"
set +e
for i in $(seq 1 30); do
    docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050', timeout=2)" </dev/null >/dev/null 2>&1
    if [[ $? -eq 0 ]]; then
        APP_OK="ja"
        break
    fi
    sleep 2
done
set -e

if [[ "$APP_OK" == "ja" ]]; then
    ok "App reageert weer"
else
    warn "App reageert nog niet binnen 60 sec — update wordt afgerond."
    warn "Check: cd $INSTALL_DIR && docker compose logs app"
fi

# Nginx herstarten zodat eventueel nieuwe config geladen wordt
docker compose restart nginx >/dev/null 2>&1 || true

# --- Stap 7: Oude images opruimen --------------------------------------------

echo
echo "${C_BLD}Stap 7 — Oude images opruimen${C_RST}"
docker image prune -f >/dev/null
ok "Ongebruikte images verwijderd"

# --- Klaar ------------------------------------------------------------------

echo
echo "${C_GRN}╔══════════════════════════════════════════════════════════════╗${C_RST}"
echo "${C_GRN}║  ✓ UPDATE VOLTOOID                                           ║${C_RST}"
echo "${C_GRN}╚══════════════════════════════════════════════════════════════╝${C_RST}"
echo
echo "  ${C_BLD}Was:${C_RST}      $HUIDIG_HASH"
echo "  ${C_BLD}Nu:${C_RST}       $NIEUW_HASH"
echo "  ${C_BLD}Backup:${C_RST}   ${BACKUP_BESTAND:-(geen — eerste run)}"
echo "  ${C_BLD}Log:${C_RST}      $LOG_FILE"
echo
echo "  Het update-log bevat ${C_GRN}geen${C_RST} wachtwoorden, maar je mag het wel opruimen:"
echo "      sudo rm $LOG_FILE"
echo
