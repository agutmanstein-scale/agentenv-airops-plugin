set -euo pipefail
URL=$1; shift
D=$(mktemp -d); REC=/var/lib/airops-stream/rec/airops
for spec in "$@"; do
  t=${spec%%=*}; name=${spec#*=}; [ "$name" = "$spec" ] && name=$(echo $t | tr -d ':')
  if [ ${#t} -gt 8 ]; then want=$(date -u -d "$t" +%s); else want=$(date -u -d "$(date -u +%F) $t" +%s); fi
  seg=; for f in $(ls $REC | sort); do s=$(echo $f | sed -E 's/^([0-9-]+)_([0-9]+)-([0-9]+)-([0-9]+)-.*/\1 \2:\3:\4/'); st=$(date -u -d "$s" +%s); [ $st -le $want ] && { seg=$f; off=$((want-st)); }; done
  [ -n "$seg" ] || { echo "no segment for $t"; continue; }
  ffmpeg -nostdin -loglevel error -ss $off -i $REC/$seg -frames:v 1 -q:v 3 $D/$name.jpg && echo "$name: $seg +${off}s"
done
tar -cf /tmp/grab.tar -C $D . && curl -fsS -X PUT --upload-file /tmp/grab.tar "$URL"; rm -rf $D /tmp/grab.tar
