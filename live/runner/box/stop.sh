set -uo pipefail
case ${1:-} in
  now) systemctl kill --kill-who=main -s TERM airops-live; sleep 2; systemctl kill --kill-who=main -s TERM airops-live
       echo "airops-live: stopping the day now, then exiting (agent-env tears the day down first)" ;;
  cancel) rm -f /home/ubuntu/live/stop.flag; echo "stop cancelled: the runner goes on" ;;
  *) sudo -u ubuntu touch /home/ubuntu/live/stop.flag; echo "airops-live finishes the day in progress, then exits" ;;
esac
sleep 3; tail -3 /home/ubuntu/live/runner.log
