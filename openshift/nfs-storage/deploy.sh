#!/bin/bash

# NFS Storage Deployment Script for OpenShift

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
if ! command -v oc &>/dev/null; then
    print_error "oc CLI not found. Please install OpenShift CLI."
    exit 1
fi

if ! oc whoami &>/dev/null; then
    print_error "Not logged into OpenShift. Please run 'oc login' first."
    exit 1
fi

echo ""
echo "=========================================="
echo "NFS Storage Setup for OpenShift"
echo "=========================================="
echo ""

# Get NFS server details
read -p "Enter NFS Server IP address: " NFS_SERVER_IP
read -p "Enter NFS Export Path [/nfs/exports/openshift-storage]: " NFS_EXPORT_PATH
NFS_EXPORT_PATH=${NFS_EXPORT_PATH:-/nfs/exports/openshift-storage}

if [ -z "$NFS_SERVER_IP" ]; then
    print_error "NFS Server IP is required."
    exit 1
fi

print_info "NFS Server: $NFS_SERVER_IP"
print_info "Export Path: $NFS_EXPORT_PATH"
echo ""

read -p "Proceed with deployment? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Deployment cancelled."
    exit 0
fi

# Create temporary deployment file
TEMP_DEPLOYMENT=$(mktemp)
cp nfs-provisioner-deployment.yaml "$TEMP_DEPLOYMENT"

# Replace placeholders
print_info "Updating configuration..."
sed -i.bak "s|<NFS_SERVER_IP>|${NFS_SERVER_IP}|g" "$TEMP_DEPLOYMENT"
sed -i.bak "s|<NFS_EXPORT_PATH>|${NFS_EXPORT_PATH}|g" "$TEMP_DEPLOYMENT"

# Deploy
print_info "Deploying RBAC resources..."
oc apply -f rbac.yaml

print_info "Deploying NFS provisioner..."
oc apply -f "$TEMP_DEPLOYMENT"

print_info "Deploying StorageClass..."
oc apply -f storageclass.yaml

# Wait for provisioner
print_info "Waiting for provisioner to be ready..."
if oc wait --for=condition=ready pod -l app=nfs-client-provisioner -n nfs-provisioner --timeout=120s; then
    print_info "NFS provisioner is ready!"
else
    print_error "Provisioner failed to start. Check logs with:"
    echo "  oc logs -n nfs-provisioner -l app=nfs-client-provisioner"
    exit 1
fi

# Cleanup
rm -f "$TEMP_DEPLOYMENT" "${TEMP_DEPLOYMENT}.bak"

# Show status
echo ""
echo "=========================================="
print_info "Deployment completed successfully!"
echo "=========================================="
echo ""
echo "Provisioner Pod:"
oc get pods -n nfs-provisioner
echo ""
echo "StorageClass:"
oc get storageclass nfs-storage
echo ""
echo "Next steps:"
echo "1. Test: oc apply -f example-pvc.yaml"
echo "2. Check: oc get pvc test-nfs-claim"
echo "3. Deploy: oc apply -f example-pod.yaml"
echo ""

# Made with Bob
