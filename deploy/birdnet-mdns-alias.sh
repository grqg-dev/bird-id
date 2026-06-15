#!/bin/sh
# Publish "birdnet.local" as an mDNS (Avahi) alias pointing at BIRDNET_TARGET_IP,
# so ESP32-S3 sensors can find the bird-id ingest server by name instead of a
# hardcoded IP. This does NOT rename the host — avahi keeps advertising
# <hostname>.local; this just adds an extra A record for birdnet.local.
#
# The target IP is configurable (it need not be this host): set BIRDNET_TARGET_IP
# in /etc/default/birdnet-mdns-alias, then `systemctl restart birdnet-mdns-alias`
# to repoint the name at a new server — no ESP32 reflash needed.
#
# Run via the companion systemd unit (birdnet-mdns-alias.service).
set -eu

: "${BIRDNET_TARGET_IP:?set BIRDNET_TARGET_IP in /etc/default/birdnet-mdns-alias}"

echo "publishing birdnet.local -> $BIRDNET_TARGET_IP"
exec avahi-publish -a -R birdnet.local "$BIRDNET_TARGET_IP"
