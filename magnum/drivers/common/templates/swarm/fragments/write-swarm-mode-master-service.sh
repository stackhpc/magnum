#!/bin/sh

if [ $SWARM_MODE = "False" ]; then
    exit
fi

if [ -z "$NO_PROXY" ]; then
    NO_PROXY=$NODE_IP
fi

cat > /etc/systemd/system/swarm-manager.service << EOF
[Unit]
Description=Swarm Manager
After=docker.service
Requires=docker.service
OnFailure=swarm-manager-failure.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/docker swarm init --advertise-addr $NODE_IP
ExecStop=/usr/bin/docker swarm leave --force
ExecStartPost=/usr/local/bin/notify-heat

[Install]
WantedBy=multi-user.target
EOF

chown root:root /etc/systemd/system/swarm-manager.service
chmod 644 /etc/systemd/system/swarm-manager.service

SCRIPT=/usr/local/bin/notify-heat

cat > $SCRIPT << EOF
#!/bin/sh
JOIN_TOKEN=\$(/usr/bin/docker swarm join-token worker -q)
curl -k -i -X POST -H 'Content-Type: application/json' -H 'X-Auth-Token: $WAIT_HANDLE_TOKEN' \\
    --data-binary '{"status": "SUCCESS", "reason": "Setup complete", "data": "'\$JOIN_TOKEN'"}' \\
    "$WAIT_HANDLE_ENDPOINT"
EOF

chown root:root $SCRIPT
chmod 755 $SCRIPT
