#!/bin/sh
# usage: tools/livebatch.sh <label> <n> <parallel> <bot command...>
# runs n private play-server matches (free-for-all vs server bots), prints place per match
label=$1; n=$2; par=$3; shift 3; cmd="$*"
mkdir -p logs/live/$label
seq 1 $n | xargs -P $par -I{} sh -c "python tools/live.py --bot \"$cmd\" --map kit/arena --hello-map-dir /arena --out logs/live/$label --name $label 2>&1 | grep -m1 '^end' "
