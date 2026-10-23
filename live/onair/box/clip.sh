# clip.sh PUT_URL START=SECONDS...: cuts each [UTC start (HH:MM:SS today, or YYYY-MM-DDTHH:MM:SS), seconds] piece
# from the recording, joins them with 0.4 s fades (picture and sound), encodes H.264 + AAC and uploads the MP4.
set -euo pipefail
URL=$1; shift
REC=/var/lib/airops-stream/rec/airops; D=$(mktemp -d); i=0; : > $D/list.txt
for spec in "$@"; do
  t=${spec%=*}; dur=${spec#*=}
  if [ ${#t} -gt 8 ]; then want=$(date -u -d "$t" +%s); else want=$(date -u -d "${CLIPDAY:-$(date -u +%F)} $t" +%s); fi
  seg=; for f in $(ls $REC | sort); do s=$(echo $f | sed -E 's/^([0-9-]+)_([0-9]+)-([0-9]+)-([0-9]+)-.*/\1 \2:\3:\4/'); st=$(date -u -d "$s" +%s); [ $st -le $want ] && { seg=$f; off=$((want-st)); }; done
  i=$((i+1)); e=$(python3 -c "print($dur - 0.4)")
  nice -n 10 ffmpeg -nostdin -loglevel error -ss $off -i $REC/$seg -t $dur \
    -vf "fade=t=in:st=0:d=0.4,fade=t=out:st=$e:d=0.4" -af "afade=t=in:st=0:d=0.4,afade=t=out:st=$e:d=0.4,aresample=48000" \
    -c:v h264_nvenc -preset p5 -rc vbr -cq 23 -b:v 8M -maxrate 10M -pix_fmt yuv420p -g 60 -c:a aac -b:a 192k -ar 48000 $D/p$i.mp4
  echo "file '$D/p$i.mp4'" >> $D/list.txt; echo "piece $i: $t +${dur}s from $seg at ${off}s"
done
nice -n 10 ffmpeg -nostdin -loglevel error -f concat -safe 0 -i $D/list.txt -c copy -movflags +faststart $D/clip.mp4
ffprobe -v error -show_entries format=duration,size -of compact $D/clip.mp4
ffmpeg -nostdin -hide_banner -i $D/clip.mp4 -vn -af ebur128=peak=true -f null - 2>&1 | grep -A14 "Summary:" | grep -E "I:|Peak:"
curl -fsS -X PUT --upload-file $D/clip.mp4 "$URL" && echo uploaded; rm -rf $D
