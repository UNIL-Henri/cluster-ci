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

update_env() {
    local var_name=$1
    local var_value=$2
    if grep -q "^${var_name}=" "$ENV_FILE"; then
        local tmp_env=$(mktemp)
        grep -v "^${var_name}=" "$ENV_FILE" > "$tmp_env"
        echo "${var_name}=\"${var_value}\"" >> "$tmp_env"
        mv "$tmp_env" "$ENV_FILE"
    else
        echo "${var_name}=\"${var_value}\"" >> "$ENV_FILE"
    fi
}

update_needed=false

if [ -z "$HEADNODE_IP" ] || [ -z "$HEADNODE_USER" ] || [ -z "$HEADNODE_PASS" ]; then
    echo "--- Configuration du Headnode ---"
    [ -z "$HEADNODE_IP" ] && read -p "IP du Headnode : " HEADNODE_IP
    [ -z "$HEADNODE_USER" ] && read -p "Utilisateur SSH : " HEADNODE_USER
    [ -z "$HEADNODE_PASS" ] && read -rs -p "Mot de passe SSH : " HEADNODE_PASS
    echo ""
    update_needed=true
fi

if [ -z "$TARGET_REPO" ]; then
    read -p "Cible GitHub (ex: UNIL-DESI) : " TARGET_REPO
    [ -z "$TARGET_REPO" ] && TARGET_REPO="UNIL-DESI"
    update_needed=true
fi

if [ -z "$WORKER_COUNT" ]; then
    WORKER_COUNT=0
    echo "--- Configuration des Workers ---"
else
    echo "--- $WORKER_COUNT Worker(s) existant(s) trouvé(s) dans la configuration ---"
fi

while true; do
    if [ "$WORKER_COUNT" -eq 0 ]; then
        prompt_msg="Ajouter un worker ? (O/n) : "
    else
        prompt_msg="Ajouter un nouveau worker supplémentaire ? (o/N) : "
    fi
    
    read -p "$prompt_msg" add_worker
    
    if [ "$WORKER_COUNT" -eq 0 ]; then
        if [[ "$add_worker" =~ ^[Nn] ]]; then
            break
        fi
    else
        if [[ ! "$add_worker" =~ ^[OoYy] ]]; then
            break
        fi
    fi
    
    WORKER_COUNT=$((WORKER_COUNT + 1))
    read -p "IP du Worker $WORKER_COUNT : " w_ip
    read -p "Utilisateur SSH : " w_user
    read -rs -p "Mot de passe SSH : " w_pass
    echo ""
    
    export "WORKER_${WORKER_COUNT}_IP"="$w_ip"
    export "WORKER_${WORKER_COUNT}_USER"="$w_user"
    export "WORKER_${WORKER_COUNT}_PASS"="$w_pass"
    update_needed=true
done

if [ "$update_needed" = true ]; then
    echo "📝 Sauvegarde des identifiants dans $ENV_FILE..."
    touch "$ENV_FILE"
    
    update_env "HEADNODE_IP" "$HEADNODE_IP"
    update_env "HEADNODE_USER" "$HEADNODE_USER"
    update_env "HEADNODE_PASS" "$HEADNODE_PASS"
    update_env "TARGET_REPO" "$TARGET_REPO"
    update_env "WORKER_COUNT" "$WORKER_COUNT"
    
    for ((i=1; i<=WORKER_COUNT; i++)); do
        ip_var="WORKER_${i}_IP"
        user_var="WORKER_${i}_USER"
        pass_var="WORKER_${i}_PASS"
        update_env "$ip_var" "${!ip_var}"
        update_env "$user_var" "${!user_var}"
        update_env "$pass_var" "${!pass_var}"
    done
fi

echo "==========================================================="
echo "🚀 Mise à jour du Headnode ($HEADNODE_IP)..."
echo "==========================================================="
export SSHPASS="$HEADNODE_PASS"
sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$HEADNODE_USER@$HEADNODE_IP" "export SUDO_PASSWORD='$HEADNODE_PASS'; curl -sSL https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh | bash -s -- headnode $TARGET_REPO"

for ((i=1; i<=WORKER_COUNT; i++)); do
    ip_var="WORKER_${i}_IP"
    user_var="WORKER_${i}_USER"
    pass_var="WORKER_${i}_PASS"
    
    ip_val="${!ip_var}"
    user_val="${!user_var}"
    pass_val="${!pass_var}"
    
    if [ -n "$ip_val" ]; then
        echo "==========================================================="
        echo "🚀 Mise à jour du Worker $i ($ip_val)..."
        echo "==========================================================="
        export SSHPASS="$pass_val"
        sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$user_val@$ip_val" "export SUDO_PASSWORD='$pass_val'; curl -sSL https://raw.githubusercontent.com/UNIL-DESI/cluster-ci/main/install.sh | bash -s -- worker"
    fi
done

echo "✅ Mise à jour du cluster terminée !"

echo "==========================================================="
echo "🧪 Test de l'infrastructure : Soumission de 2 jobs de test..."
echo "==========================================================="
echo "Mise en pause de 10s pour laisser le temps aux services de démarrer..."
sleep 10

echo "🚀 Soumission du Job 1..."
uv run src/scheduler/submit_job.py "$TARGET_REPO/cluster-ci" "main" --headnode "http://$HEADNODE_IP:5000" &
JOB1=$!

echo "🚀 Soumission du Job 2..."
uv run src/scheduler/submit_job.py "$TARGET_REPO/cluster-ci" "main" --headnode "http://$HEADNODE_IP:5000" &
JOB2=$!

wait $JOB1
wait $JOB2

echo "🎉 Test du cluster terminé ! Tous les noeuds sont opérationnels."
