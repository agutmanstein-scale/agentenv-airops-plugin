# logs.sh [N]: the last N lines (default 8) of the live server's and the runner's logs and the fresh watchdog's.
echo "--- server.log"; tail -${1:-8} /home/ubuntu/live/server.log
echo "--- runner.log"; tail -${1:-8} /home/ubuntu/live/runner.log
echo "--- fresh.jsonl"; tail -${1:-8} /var/log/airops-stream/fresh.jsonl
