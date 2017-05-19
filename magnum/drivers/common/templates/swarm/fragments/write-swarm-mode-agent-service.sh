#!/bin/sh

. /etc/sysconfig/heat-params

if [[ ${SWARM_MODE} = "False" ]]; then
    exit
fi

CONF_FILE=/etc/systemd/system/swarm-agent.service

cat > $CONF_FILE << EOF
[Unit]
Description=Swarm Agent
After=docker.service
Requires=docker.service
OnFailure=swarm-agent-failure.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/join-swarm
ExecStop=/usr/bin/docker swarm leave
ExecStartPost=/usr/local/bin/notify-heat

[Install]
WantedBy=multi-user.target
EOF

chown root:root $CONF_FILE
chmod 644 $CONF_FILE

SCRIPT=/usr/local/bin/notify-heat

cat > $SCRIPT << EOF
#!/bin/sh
curl -k -i -X POST -H 'Content-Type: application/json' -H 'X-Auth-Token: $WAIT_HANDLE_TOKEN' \
    --data-binary '{"status": "SUCCESS", "reason": "Swarm agent ready", "data": "OK"}' \
    "$WAIT_HANDLE_ENDPOINT"
EOF

chown root:root $SCRIPT
chmod 755 $SCRIPT

# Each metadata signal adds an item with an incrementing key to the data.
# For some reason there is a null entry for key 1 and the data takes key 2.
JOIN_TOKEN=$(echo $SWARM_MODE_JOIN_TOKEN | python -c 'import json,sys; print json.loads(sys.stdin.read())["2"]')

JOIN_SWARM_SCRIPT=/usr/local/bin/join-swarm

cat > $JOIN_SWARM_SCRIPT << EOF
#!/bin/sh
/usr/bin/docker swarm join --token ${JOIN_TOKEN} ${SWARM_API_IP}:2377
EOF

chown root:root $JOIN_SWARM_SCRIPT
chmod 755 $JOIN_SWARM_SCRIPT
