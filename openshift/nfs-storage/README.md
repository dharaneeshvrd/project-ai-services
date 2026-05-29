# NFS Storage for OpenShift

Simple setup for using an external NFS server as storage in OpenShift.

## Prerequisites

- External NFS server (Linux machine with NFS installed)
- OpenShift cluster with admin access
- Network connectivity between OpenShift nodes and NFS server

## Setup Overview

1. Configure NFS server (external machine)
2. Deploy NFS provisioner in OpenShift
3. Test with example Nginx application

## Step 1: Configure NFS Server

SSH to your NFS server and run:

### RHEL/CentOS/Fedora:
```bash
# Install NFS server
sudo dnf install -y nfs-utils

# Create export directory
sudo mkdir -p /nfs/exports/openshift-storage
sudo chmod 777 /nfs/exports/openshift-storage

# Configure exports
echo "/nfs/exports/openshift-storage *(rw,sync,no_root_squash,no_subtree_check,insecure)" | sudo tee -a /etc/exports

# Start NFS server
sudo systemctl enable --now nfs-server
sudo exportfs -arv

# Configure firewall
sudo firewall-cmd --permanent --add-service=nfs
sudo firewall-cmd --permanent --add-service=rpc-bind
sudo firewall-cmd --permanent --add-service=mountd
sudo firewall-cmd --reload
```

### Ubuntu/Debian:
```bash
# Install NFS server
sudo apt-get update
sudo apt-get install -y nfs-kernel-server

# Create export directory
sudo mkdir -p /nfs/exports/openshift-storage
sudo chmod 777 /nfs/exports/openshift-storage

# Configure exports
echo "/nfs/exports/openshift-storage *(rw,sync,no_root_squash,no_subtree_check,insecure)" | sudo tee -a /etc/exports

# Start NFS server
sudo systemctl enable --now nfs-kernel-server
sudo exportfs -arv
```

### Verify NFS Server:
```bash
sudo exportfs -v
showmount -e localhost
```

## Step 2: Deploy NFS Provisioner in OpenShift

### Option A: Using Deploy Script (Recommended)
```bash
cd openshift/nfs-storage
./deploy.sh
# Enter your NFS server IP and path when prompted
```

### Option B: Manual Deployment
```bash
cd openshift/nfs-storage

# Set your NFS server details
export NFS_SERVER_IP="192.168.1.100"  # Replace with your NFS server IP
export NFS_EXPORT_PATH="/nfs/exports/openshift-storage"

# Update deployment file
sed -i.bak "s|<NFS_SERVER_IP>|${NFS_SERVER_IP}|g" nfs-provisioner-deployment.yaml
sed -i.bak "s|<NFS_EXPORT_PATH>|${NFS_EXPORT_PATH}|g" nfs-provisioner-deployment.yaml

# Deploy
oc apply -f rbac.yaml
oc apply -f nfs-provisioner-deployment.yaml
oc apply -f storageclass.yaml

# Wait for provisioner to be ready
oc wait --for=condition=ready pod -l app=nfs-client-provisioner -n nfs-provisioner --timeout=120s
```

## Step 3: Verify Installation

```bash
# Check provisioner pod
oc get pods -n nfs-provisioner

# Check StorageClass
oc get storageclass nfs-storage

# View logs
oc logs -n nfs-provisioner -l app=nfs-client-provisioner
```

## Step 4: Test with Nginx

```bash
# Create PVC
oc apply -f example-pvc.yaml

# Check PVC status (should be Bound)
oc get pvc test-nfs-claim

# Deploy Nginx
oc apply -f example-pod.yaml

# Test write
oc exec test-nfs-pod -- sh -c "echo 'Hello NFS' > /usr/share/nginx/html/index.html"

# Test read
oc exec test-nfs-pod -- cat /usr/share/nginx/html/index.html

# Verify on NFS server
ssh <nfs-server>
ls -la /nfs/exports/openshift-storage/
```

## Using NFS Storage in Your Applications

Simply specify `storageClassName: nfs-storage` in your PVC:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: my-app-storage
spec:
  storageClassName: nfs-storage
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 5Gi
```

## Troubleshooting

### Provisioner Pod Not Starting
```bash
# Check pod events
oc describe pod -n nfs-provisioner -l app=nfs-client-provisioner

# Check logs
oc logs -n nfs-provisioner -l app=nfs-client-provisioner

# Test NFS connectivity from OpenShift node
oc debug node/<node-name>
chroot /host
mount -t nfs <NFS_SERVER_IP>:/nfs/exports/openshift-storage /mnt
```

### PVC Stuck in Pending
```bash
# Check PVC events
oc describe pvc <pvc-name>

# Check provisioner logs
oc logs -n nfs-provisioner -l app=nfs-client-provisioner
```

### Permission Denied
```bash
# On NFS server, fix permissions
sudo chmod 777 /nfs/exports/openshift-storage/

# Check SELinux (RHEL/CentOS)
sudo setsebool -P nfs_export_all_rw 1
```

## Cleanup

```bash
# Using cleanup script
./cleanup.sh

# Or manually
oc delete -f example-pod.yaml
oc delete -f example-pvc.yaml
oc delete -f storageclass.yaml
oc delete -f nfs-provisioner-deployment.yaml
oc delete -f rbac.yaml
```

## Files

- **rbac.yaml** - RBAC resources for provisioner
- **nfs-provisioner-deployment.yaml** - NFS client provisioner
- **storageclass.yaml** - StorageClass definition
- **example-pvc.yaml** - Example PVC for testing
- **example-pod.yaml** - Example Nginx pod
- **deploy.sh** - Automated deployment script
- **cleanup.sh** - Cleanup script

## Notes

- NFS server must be accessible from all OpenShift nodes
- Default export path: `/nfs/exports/openshift-storage`
- Default StorageClass name: `nfs-storage`
- Supports ReadWriteMany (multiple pods can access simultaneously)