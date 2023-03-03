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
sudo apt-get install git vim apparmor apparmor-utils -y

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
enable_plugin magnum https://github.com/stackhpc/magnum johng-cluster-api-merge-yoga2
enable_plugin magnum-ui https://opendev.org/openstack/magnum-ui
enable_plugin octavia https://opendev.org/openstack/octavia
enable_plugin octavia-dashboard https://opendev.org/openstack/octavia-dashboard
LIBS_FROM_GIT+=python-octaviaclient
DATABASE_PASSWORD=secretdatabase
RABBIT_PASSWORD=secretrabbit
ADMIN_PASSWORD=secretadmin
HOST_IP=10.0.3.91 #change this 
SERVICE_PASSWORD=secretservice
SERVICE_TOKEN=111222333444
# Enable Logging
LOGFILE=/opt/stack/logs/stack.sh.log
VERBOSE=True
LOG_COLOR=True
# Octavia services
enable_service octavia o-api o-cw o-da o-hk o-hm
enable_service tempest
GLANCE_LIMIT_IMAGE_SIZE_TOTAL=10000
LIBVIRT_TYPE=kvm

[[post-config|/etc/neutron/neutron.conf]]
[DEFAULT]
advertise_mtu = True
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
source /opt/stack/openrc admin

# Add k8s image
curl -O https://minio.services.osism.tech/openstack-k8s-capi-images/ubuntu-2004-kube-v1.25/ubuntu-2004-kube-v1.25.5.qcow2
openstack image create ubuntu-2004-kube-v1.25.5 \
  --file ubuntu-2004-kube-v1.25.5.qcow2 \
  --disk-format qcow2 \
  --container-format bare \
  --public
openstack image set ubuntu-2004-kube-v1.25.5 --os-distro ubuntu --os-version 20.04

# Register template (new driver)
# TODO: use image that devstack magnum plugin installs (os_distro=ubuntu)
openstack coe cluster template create new_driver \
  --coe kubernetes \
  --image $(openstack image show ubuntu-2004-kube-v1.25.5 -c id -f value) \
  --external-network public \
  --label kube_tag=v1.25.5 \
  --master-flavor ds2G \
  --flavor ds2G \
  --public \
  --master-lb-enabled

#old driver 
openstack coe cluster template create old_driver \
  --coe kubernetes \
  --image fedora-coreos-35.20220116.3.0-openstack.x86_64 \
  --external-network public \
  --master-flavor ds2G \
  --flavor ds2G \
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
