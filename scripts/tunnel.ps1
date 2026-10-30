# Start the Cloudflare tunnel pointing to local FastAPI on :8000.
# Prereq: cloudflared installed and tunnel configured.
#   1) cloudflared tunnel login
#   2) cloudflared tunnel create gunivox
#   3) Add the tunnel ID to config.yml (see docs)
# Usage: .\scripts\tunnel.ps1

$ErrorActionPreference = 'Stop'
$tunnelName = if ($args.Count -gt 0) { $args[0] } else { 'gunivox' }
cloudflared tunnel run $tunnelName
