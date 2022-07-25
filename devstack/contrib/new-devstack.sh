#!/bin/bash
#
# These instructions assume an Ubuntu-based host or VM for running devstack.
# Please note that if you are running this in a VM, it is vitally important
# that the underlying hardware have nested virtualization enabled or you will
# experience very poor amphora performance.
#
# Heavily based on:
# https://opendev.org/openstack/octavia/src/branch/master/devstack/contrib/new-octavia-devstack.sh

set -ex

# Set up the packages we need. Ubuntu package manager is assumed.
sudo apt-get update
sudo apt-get install git vim -y

# Clone the devstack repo
sudo mkdir -p /opt/stack
if [ ! -f /opt/stack/stack.sh ]; then
    sudo chown -R ${USER}. /opt/stack
    git clone https://git.openstack.org/openstack-dev/devstack /opt/stack
fi

cat <<EOF > /opt/stack/local.conf
[[local|localrc]]
enable_plugin barbican https://opendev.org/openstack/barbican
enable_plugin heat https://opendev.org/openstack/heat
enable_plugin neutron https://opendev.org/openstack/neutron
enable_plugin magnum https://opendev.org/openstack/magnum
enable_plugin magnum-ui https://opendev.org/openstack/magnum-ui
enable_plugin octavia https://opendev.org/openstack/octavia
enable_plugin octavia-dashboard https://opendev.org/openstack/octavia-dashboard
LIBS_FROM_GIT+=python-octaviaclient

DATABASE_PASSWORD=secretdatabase
RABBIT_PASSWORD=secretrabbit
ADMIN_PASSWORD=secretadmin
SERVICE_PASSWORD=secretservice
SERVICE_TOKEN=111222333444
# Enable Logging
LOGFILE=/opt/stack/logs/stack.sh.log
VERBOSE=True
LOG_COLOR=True

# Octavia services
enable_service octavia o-api o-cw o-da o-hk o-hm
GLANCE_LIMIT_IMAGE_SIZE_TOTAL=10000
LIBVIRT_TYPE=kvm

# Add magnum patch e.g.
#MAGNUM_REPO=https://review.opendev.org/openstack/magnum
#MAGNUM_BRANCH=refs/changes/76/851076/10

[[post-config|/etc/neutron/neutron.conf]]
[DEFAULT]
advertise_mtu = True
global_physnet_mtu = 1400
EOF

# Fix permissions on current tty so screens can attach
sudo chmod go+rw `tty`

# Stack that stack!
/opt/stack/stack.sh

#
# Install this checkout and restart the Magnum services
#
SELF_PATH="$(realpath "${BASH_SOURCE[0]:-${(%):-%x}}")"
REPO_PATH="$(dirname "$(dirname "$(dirname "$SELF_PATH")")")"
python3 -m pip install -e "$REPO_PATH"
sudo systemctl restart devstack@magnum-api devstack@magnum-cond

#
# Setup k8s for Cluster API
#

# # Install `kubectl` CLI
curl -fsLo /tmp/kubectl "https://dl.k8s.io/release/$(curl -fsL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 /tmp/kubectl /usr/local/bin/kubectl

# Install k3s
curl -fsL https://get.k3s.io | sudo bash -s - --disable traefik

# copy kubeconfig file into standard location
mkdir -p $HOME/.kube
sudo cp /etc/rancher/k3s/k3s.yaml $HOME/.kube/config
sudo chown $USER $HOME/.kube/config

# Install helm
curl -fsL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# Install cert manager
helm upgrade cert-manager cert-manager \
  --install \
  --namespace cert-manager \
  --create-namespace \
  --repo https://charts.jetstack.io \
  --version v1.10.1 \
  --set installCRDs=true \
  --wait

# Install Cluster API resources
mkdir -p capi
cat <<EOF > capi/kustomization.yaml
---
resources:
  - https://github.com/kubernetes-sigs/cluster-api/releases/download/v1.3.1/cluster-api-components.yaml
  - https://github.com/stackhpc/cluster-api-provider-openstack/releases/download/v0.7.0-stackhpc.3/infrastructure-components.yaml
patches:
  - patch: |-
      - op: replace
        path: /spec/template/spec/containers/0/args
        value:
          - --leader-elect
          - --metrics-bind-addr=localhost:8080
    target:
      kind: Deployment
      namespace: capi-system
      name: capi-controller-manager
  - patch: |-
      - op: replace
        path: /spec/template/spec/containers/0/args
        value:
          - --leader-elect
          - --metrics-bind-addr=localhost:8080
    target:
      kind: Deployment
      namespace: capi-kubeadm-bootstrap-system
      name: capi-kubeadm-bootstrap-controller-manager
  - patch: |-
      - op: replace
        path: /spec/template/spec/containers/0/args
        value:
          - --leader-elect
          - --metrics-bind-addr=localhost:8080
    target:
      kind: Deployment
      namespace: capi-kubeadm-control-plane-system
      name: capi-kubeadm-control-plane-controller-manager
EOF
kubectl apply -k capi

# Install addon manager
helm upgrade cluster-api-addon-provider cluster-api-addon-provider \
  --install \
  --repo https://stackhpc.github.io/cluster-api-addon-provider \
  --version 0.1.0-dev.0.main.21 \
  --namespace capi-addon-system \
  --create-namespace \
  --wait \
  --timeout 30m

source /opt/stack/openrc admin admin

pip install python-magnumclient

# Add k8s image
curl -O https://minio.services.osism.tech/openstack-k8s-capi-images/ubuntu-2004-kube-v1.25/ubuntu-2004-kube-v1.25.5.qcow2
openstack image create ubuntu-2004-kube-v1.25.5 \
  --file ubuntu-2004-kube-v1.25.5.qcow2 \
  --disk-format qcow2 \
  --container-format bare \
  --public
openstack image set ubuntu-2004-kube-v1.25.5 --os-distro ubuntu --os-version 20.04

# Register template
openstack coe cluster template create kube-1.25.5 \
  --coe kubernetes \
  --image $(openstack image show ubuntu-2004-kube-v1.25.5 -c id -f value) \
  --external-network public \
  --label kube_tag=v1.25.5 \
  --master-flavor ds4G \
  --flavor ds4G \
  --public \
  --master-lb-enabled

exit

# Test it
openstack coe cluster create devstacktest \
  --cluster-template kube-1.25.5 \
  --master-count 3 \
  --node-count 2
openstack coe cluster list

# Get creds
openstack coe cluster config devstacktest
# TODO: run sonoboy
