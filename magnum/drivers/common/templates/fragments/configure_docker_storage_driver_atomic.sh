# This file contains docker storage drivers configuration for fedora
# atomic hosts, as supported by Magnum.

# Determine whether the installed docker has the docker-storage-setup utility.
has_dss () {
    which docker-storage-setup >/dev/null 2>&1
}

# * Remove any existing docker-storage configuration. In case of an
#   existing configuration, docker-storage-setup will fail.
# * Remove docker storage graph
clear_docker_storage () {
    # stop docker
    systemctl stop docker
    systemctl disable docker-storage-setup
    # clear storage graph
    rm -rf /var/lib/docker/*

    if has_dss; then
        clear_docker_storage_dss
    else
        clear_docker_storage_no_dss
    fi
}

clear_docker_storage_dss () {
    # remove current LVs
    docker-storage-setup --reset


    if [ -f /etc/sysconfig/docker-storage ]; then
        sed -i "/^DOCKER_STORAGE_OPTIONS=/ s/=.*/=/" /etc/sysconfig/docker-storage
    fi
}

clear_docker_storage_no_dss () {
    if [[ -e /dev/mapper/docker-thinpool ]]; then
        lvchange -an docker/thinpool
        lvremove docker/thinpool
    fi
}

# Configure generic docker storage driver.
configure_storage_driver_generic() {
    if has_dss; then
        configure_storage_driver_generic_dss
    else
        configure_storage_driver_generic_no_dss
    fi
}

configure_storage_driver_generic_dss () {
    clear_docker_storage

    if [ -n "$DOCKER_VOLUME_SIZE" ] && [ "$DOCKER_VOLUME_SIZE" -gt 0 ]; then
        mkfs.xfs -f ${device_path}
        echo "${device_path} /var/lib/docker xfs defaults 0 0" >> /etc/fstab
        mount -a
    fi

    echo "DOCKER_STORAGE_OPTIONS=\"--storage-driver $1\"" > /etc/sysconfig/docker-storage
}

configure_storage_driver_generic_no_dss () {
    # Configure dockerd to use the devicemapper volume.
    cat | python << EOF
import json

try:
    with open("/etc/docker/daemon.json") as f:
        opts = json.load(f)
except IOError:
    opts = {}

opts["storage-driver"] = "$DOCKER_STORAGE_DRIVER"

with open("/etc/docker/daemon.json", "w") as f:
    json.dump(opts, f, sort_keys=True, indent=4)
EOF
}

# Configure docker storage with devicemapper using direct LVM
configure_devicemapper () {
    clear_docker_storage

    if has_dss; then
        configure_devicemapper_dss
    else
        configure_devicemapper_no_dss
    fi
}

configure_devicemapper_dss() {
    echo "GROWROOT=True" > /etc/sysconfig/docker-storage-setup
    echo "STORAGE_DRIVER=devicemapper" >> /etc/sysconfig/docker-storage-setup

    if [ -n "$DOCKER_VOLUME_SIZE" ] && [ "$DOCKER_VOLUME_SIZE" -gt 0 ]; then

        pvcreate -f ${device_path}
        vgcreate docker ${device_path}

        echo "VG=docker" >> /etc/sysconfig/docker-storage-setup
    else
        echo "ROOT_SIZE=5GB" >> /etc/sysconfig/docker-storage-setup
        echo "DATA_SIZE=95%FREE" >> /etc/sysconfig/docker-storage-setup
    fi

    docker-storage-setup
}

configure_devicemapper_no_dss () {
    if [ -n "$DOCKER_VOLUME_SIZE" ] && [ "$DOCKER_VOLUME_SIZE" -gt 0 ]; then

        pvcreate -f ${device_path}
        vgcreate docker ${device_path}
        lvcreate --wipesignatures y -n thinpool docker -l 95%VG
        lvcreate --wipesignatures y -n thinpoolmeta docker -l 1%VG
        lvconvert -y \
          --zero n \
          -c 512K \
          --thinpool docker/thinpool \
          --poolmetadata docker/thinpoolmeta
        cat >> /etc/lvm/profile/docker-thinpool.profile << EOF
thin_pool_autoextend_threshold = 80
activation {
  thin_pool_autoextend_threshold=80
  thin_pool_autoextend_percent=20
}
EOF
        lvchange --metadataprofile docker-thinpool docker/thinpool
        lvs -o+seg_monitor

        # Configure dockerd to use the devicemapper volume.
        cat | python << EOF
import json

try:
    with open("/etc/docker/daemon.json") as f:
        opts = json.load(f)
except IOError:
    opts = {}

opts["storage-driver"] = "devicemapper"
opts["storage-opts"] = [
    "dm.thinpooldev=/dev/mapper/docker-thinpool",
    "dm.use_deferred_removal=true",
    "dm.use_deferred_deletion=true"
]

with open("/etc/docker/daemon.json", "w") as f:
    json.dump(opts, f, sort_keys=True, indent=4)
EOF
    else
        echo "Error: must use an ephemeral partition with devicemapper"
        exit 1
    fi
}
