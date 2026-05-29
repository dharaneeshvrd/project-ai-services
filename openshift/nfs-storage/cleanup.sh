#!/bin/bash

# NFS Storage Cleanup Script for OpenShift

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
if ! command -v oc &>/dev/null; then
    print_error "oc CLI not found."
    exit 1
fi

if ! oc whoami &>/dev/null; then
    print_error "Not logged into OpenShift."
    exit 1
fi

echo ""
echo "=========================================="
echo "NFS Storage Cleanup"
echo "=========================================="
echo ""

print_warn "This will remove:"
echo "  - NFS provisioner"
echo "  - StorageClass"
echo "  - RBAC resources"
echo ""

read -p "Proceed with cleanup? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Cleanup cancelled."
    exit 0
fi

# Check for existing PVCs
print_info "Checking for PVCs using nfs-storage..."
PVCS=$(oc get pvc --all-namespaces -o json 2>/dev/null | jq -r '.items[] | select(.spec.storageClassName=="nfs-storage") | "\(.metadata.namespace)/\(.metadata.name)"' || echo "")

if [ -n "$PVCS" ]; then
    print_warn "Found PVCs using nfs-storage:"
    echo "$PVCS"
    echo ""
    read -p "Delete these PVCs? (yes/no): " DELETE_PVCS
    if [ "$DELETE_PVCS" = "yes" ]; then
        echo "$PVCS" | while read pvc; do
            namespace=$(echo "$pvc" | cut -d'/' -f1)
            name=$(echo "$pvc" | cut -d'/' -f2)
            print_info "Deleting PVC $name in $namespace..."
            oc delete pvc "$name" -n "$namespace" --ignore-not-found=true
        done
    fi
fi

# Delete resources
print_info "Deleting example resources..."
oc delete -f example-pod.yaml --ignore-not-found=true 2>/dev/null || true
oc delete -f example-pvc.yaml --ignore-not-found=true 2>/dev/null || true

print_info "Deleting StorageClass..."
oc delete -f storageclass.yaml --ignore-not-found=true

print_info "Deleting NFS provisioner..."
oc delete -f nfs-provisioner-deployment.yaml --ignore-not-found=true

print_info "Deleting RBAC resources..."
oc delete -f rbac.yaml --ignore-not-found=true

echo ""
echo "=========================================="
print_info "Cleanup completed!"
echo "=========================================="
echo ""

# Made with Bob
