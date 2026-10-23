# loudness.sh FROM TO [OUT.wav]: loudness (EBU R128 integrated, LRA, true peak) and silences over the recording between
# two UTC times (anything `date -d` reads, e.g. "2026-10-23 16:00"); OUT.wav keeps the audio on the box.
set -uo pipefail
REC=/var/lib/airops-stream/rec/airops; from=$(date -u -d "$1" +%s); to=$(date -u -d "$2" +%s)
D=$(mktemp -d); : > $D/list.txt; i=0
segs=$(for f in $(ls $REC | sort); do s=$(echo $f | sed -E 's/^([0-9-]+)_([0-9]+)-([0-9]+)-([0-9]+)-.*/\1 \2:\3:\4/'); echo "$(date -u -d "$s" +%s) $f"; done)
prev=; echo "$segs" | while read st f; do echo "$st $f"; done > $D/segs
n=$(wc -l < $D/segs)
for k in $(seq 1 $n); do
  read st f <<< "$(sed -n ${k}p $D/segs)"; nx=$(sed -n $((k+1))p $D/segs | cut -d' ' -f1); [ -n "$nx" ] || nx=9999999999
  [ $nx -le $from ] || [ $st -ge $to ] && continue
  a=$(( from > st ? from - st : 0 )); b=$(( (to < nx ? to : nx) - st ))
  nice -n 15 ffmpeg -nostdin -loglevel error -ss $a -i $REC/$f -t $((b-a)) -vn -ac 2 -ar 48000 -c:a pcm_s16le $D/p$k.wav && echo "file '$D/p$k.wav'" >> $D/list.txt
  echo "segment $f: ${a}s to ${b}s"
done
nice -n 15 ffmpeg -nostdin -loglevel error -f concat -safe 0 -i $D/list.txt -c copy $D/all.wav
echo "audio: $(ffprobe -v error -show_entries format=duration -of csv=p=0 $D/all.wav) s"
nice -n 15 ffmpeg -nostdin -hide_banner -i $D/all.wav -af ebur128=peak=true -f null - 2>&1 | grep -A14 "Summary:" | grep -E "I:|LRA:|Peak:"
nice -n 15 ffmpeg -nostdin -hide_banner -i $D/all.wav -af silencedetect=n=-45dB:d=1 -f null - 2>&1 | grep -oE "silence_(start|end): [0-9.]+( \| silence_duration: [0-9.]+)?" | head -40
[ -n "${3:-}" ] && cp $D/all.wav "$3"
rm -rf $D
