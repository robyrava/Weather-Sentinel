#!/bin/bash
# weather-sentinel-deploy.sh - Script per il deployment completo di Weather Sentinel su Kubernetes

set -e # Termina lo script se qualsiasi comando fallisce

# Directory contenente i file k8s
K8S_DIR="./k8s"

# Colori per output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Weather Sentinel - Deployment su Kubernetes ===${NC}"

# Verifica prerequisiti
echo -e "${YELLOW}Verifica prerequisiti...${NC}"
command -v kind >/dev/null 2>&1 || { echo -e "${RED}Kind non trovato. Installalo prima di procedere.${NC}"; exit 1; }
command -v kubectl >/dev/null 2>&1 || { echo -e "${RED}Kubectl non trovato. Installalo prima di procedere.${NC}"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo -e "${RED}Docker non trovato. Installalo prima di procedere.${NC}"; exit 1; }

# Creazione del cluster Kind se non esiste
if ! kind get clusters | grep -q "weather-sentinel"; then
  echo -e "${YELLOW}Creazione del cluster Kind 'weather-sentinel'...${NC}"
  cat <<EOF | kind create cluster --name weather-sentinel --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 30080
    hostPort: 8080
    protocol: TCP
- role: worker
- role: worker
EOF
  echo -e "${GREEN}Cluster Kind creato.${NC}"
else
  echo -e "${GREEN}Cluster Kind 'weather-sentinel' già esistente.${NC}"
fi

# Assicurarsi che kubectl stia utilizzando il contesto corretto
kubectl config use-context kind-weather-sentinel

# Creazione del namespace
echo -e "${YELLOW}Creazione del namespace...${NC}"
kubectl apply -f ${K8S_DIR}/namespace.yaml

# Deployment dei componenti infrastrutturali
echo -e "${YELLOW}Deployment di ConfigMap e Secrets...${NC}"
kubectl apply -f ${K8S_DIR}/configmap.yaml
kubectl apply -f ${K8S_DIR}/secrets.yaml

echo -e "${YELLOW}Deployment dei database...${NC}"
kubectl apply -f ${K8S_DIR}/databases.yaml

echo -e "${YELLOW}Deployment di Zookeeper e Kafka...${NC}"
kubectl apply -f ${K8S_DIR}/kafka.yaml

# Build delle immagini dei servizi
echo -e "${YELLOW}Build delle immagini Docker...${NC}"
docker build -t sga_service_image:latest ../SGA/
docker build -t sgm_service_image:latest ../SGM/
docker build -t sed_service_image:latest ../SED/
docker build -t gls_service_image:latest ../GLS/

# Caricamento delle immagini nel cluster Kind
echo -e "${YELLOW}Caricamento delle immagini nel cluster Kind...${NC}"
kind load docker-image sga_service_image:latest --name weather-sentinel
kind load docker-image sgm_service_image:latest --name weather-sentinel
kind load docker-image sed_service_image:latest --name weather-sentinel
kind load docker-image gls_service_image:latest --name weather-sentinel

# Deployment dei servizi applicativi
echo -e "${YELLOW}Deployment dei servizi applicativi...${NC}"
kubectl apply -f ${K8S_DIR}/services.yaml

# Deployment dell'API Gateway
echo -e "${YELLOW}Deployment dell'API Gateway...${NC}"
kubectl apply -f ${K8S_DIR}/api-gateway.yaml

# Deployment del monitoraggio
echo -e "${YELLOW}Deployment del sistema di monitoraggio...${NC}"
kubectl apply -f ${K8S_DIR}/monitoring.yaml

# Attesa per il completamento
echo -e "${YELLOW}Attendi che tutti i pod siano pronti...${NC}"
kubectl wait --for=condition=ready pod --all -n weather-sentinel --timeout=300s || echo -e "${YELLOW}Timeout durante l'attesa dei pod.${NC}"

# Verifica dello stato
echo -e "${YELLOW}Stato del deployment:${NC}"
kubectl get all -n weather-sentinel

echo -e "${GREEN}=== Deployment completato! ===${NC}"
echo -e "${GREEN}Accedi all'applicazione tramite: http://localhost:8080${NC}"
echo -e "${GREEN}Verifica Prometheus con: kubectl port-forward service/prometheus 9090:9090 -n weather-sentinel${NC}"
echo -e "${GREEN}Monitoraggio dello stato dei pod: kubectl get pods -n weather-sentinel -w${NC}"