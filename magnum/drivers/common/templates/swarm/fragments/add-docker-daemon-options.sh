#!/bin/sh

. /etc/sysconfig/heat-params

if [[ -f /etc/sysconfig/docker ]]; then
    SET_LOG_DRIVER=False
else
    SET_LOG_DRIVER=True
    LOG_DRIVER=journald
fi

sed -i '/^OPTIONS=/ s#\(OPTIONS='"'"'\)#\1'"$opts"'#' /etc/sysconfig/docker

# NOTE(tobias-urdin): The live restore option is only for standalone daemons.
# If its specified the swarm init will fail so we remove it here.
# See: https://docs.docker.com/config/containers/live-restore
sed -i 's/\ --live-restore//g' /etc/sysconfig/docker

# The Docker CE distribution does not provide sufficient SELinux support.
# With SELinux enabled, non-privilged containers are unable to (for
# example) bind to privileged ports as root in the container.
SET_SELINUX_ENABLED=False

cat | python << EOF
import json

try:
    with open("/etc/docker/daemon.json") as f:
        opts = json.load(f)
except IOError:
    opts = {}

opts["hosts"] = ["fd://"]
opts["hosts"].append("tcp://0.0.0.0:2375")
if "${SET_LOG_DRIVER}" == "True":
    opts["log-driver"] = "${LOG_DRIVER}"
if "${SET_SELINUX_ENABLED}" == "True":
    opts["selinux-enabled"] = True
if "${TLS_DISABLED}" == "False":
    opts["tlsverify"] = True
    opts["tlscacert"] = "/etc/docker/ca.crt"
    opts["tlskey"] = "/etc/docker/server.key"
    opts["tlscert"] = "/etc/docker/server.crt"

with open("/etc/docker/daemon.json", "w") as f:
    json.dump(opts, f, sort_keys=True, indent=4)
EOF
