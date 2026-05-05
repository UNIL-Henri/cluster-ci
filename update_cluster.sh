#!/bin/bash
set -e

ENV_FILE=".env"

# Ensure sshpass is installed
if ! command -v sshpass &> /dev/null; then
    echo "❌ Erreur : sshpass n'est pas installé sur votre machine locale."
    echo "Installez-le avec : sudo apt-get install sshpass (ou brew install sshpass sur macOS)"
    exit 1
fi

# Load existing environment variables if they exist
if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
fi

# Prompt for missing variables
update_needed=false

if [ -z "$SSH_USER" ]; then
    read -p "Veuillez entrer l'utilisateur SSH (ex: ubuntu) : " SSH_USER
    update_needed=true
fi

if [ -z "$SSH_PASS" ]; then
    read -rs -p "Veuillez entrer le mot de passe SSH : " SSH_PASS
    echo ""
    update_needed=true
fi

if [ -z "$HEADNODE_IP" ]; then
    read -p "Veuillez entrer l'IP du Headnode : " HEADNODE_IP
    update_needed=true
fi

if [ -z "$WORKER_IPS" ]; then
    read -p "Veuillez entrer les IPs des Workers (séparées par des virgules, laissez vide s'il n'y en a pas) : " WORKER_IPS
    update_needed=true
fi

# Save variables to .env for future reuse
if [ "$update_needed" = true ]; then
    echo "📝 Sauvegarde des identifiants dans $ENV_FILE..."
    touch "$ENV_FILE"
    
    # Update or insert variables
    for var in SSH_USER SSH_PASS HEADNODE_IP WORKER_IPS; do
        if grep -q "^${var}=" "$ENV_FILE"; then
            sed -i "s/^${var}=.*/${var}=${!var}/" "$ENV_FILE"
        else
            echo "${var}=${!var}" >> "$ENV_FILE"
        fi
    done
fi

export SSHPASS="$SSH_PASS"

echo "==========================================================="
echo "🚀 Mise à jour du Headnode ($HEADNODE_IP)..."
echo "==========================================================="
sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$SSH_USER@$HEADNODE_IP" "curl -sSL https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh | bash -s -- headnode"

if [ -n "$WORKER_IPS" ]; then
    IFS=',' read -ra ADDR <<< "$WORKER_IPS"
    for IP in "${ADDR[@]}"; do
        # Remove any whitespace around IP
        IP=$(echo "$IP" | xargs)
        if [ -n "$IP" ]; then
            echo "==========================================================="
            echo "🚀 Mise à jour du Worker ($IP)..."
            echo "==========================================================="
            sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$SSH_USER@$IP" "curl -sSL https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh | bash -s -- worker"
        fi
    done
fi

echo "✅ Mise à jour du cluster terminée !"
