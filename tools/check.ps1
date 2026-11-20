# Conformance check: local python, then the real sandbox in Docker (what the scorer runs).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot)
python kit/check/check.py -- python -u bot.py
if ($LASTEXITCODE) { exit 1 }
docker build -t mech-royale .
python kit/check/check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "${PWD}/kit/arena:/arena:ro" mech-royale
