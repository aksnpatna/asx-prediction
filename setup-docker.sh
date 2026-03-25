#!/bin/bash
mkdir -p /etc/docker
cat > /etc/docker/daemon.json << EOF
{"userland-proxy": false}
EOF
cat /etc/docker/daemon.json
sudo systemctl restart docker 2>/dev/null || sudo service docker restart
echo "Docker restarted"

