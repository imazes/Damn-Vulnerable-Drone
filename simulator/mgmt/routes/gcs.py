# simulator/mgmt/routes/gcs.py
import time
from flask import make_response
from docker.errors import NotFound
from . import bp
from .utils import get_container

LAUNCH_CHECK_SECS = 4      # short, bounded verification window
CHECK_INTERVAL = 0.4

@bp.route("/qgc", methods=["POST"])
def open_qgc():
    container_name = "ground-control-station"
    script_path = "/usr/local/bin/launch_qgc.sh"

    try:
        container = get_container(container_name)
        if container.status != "running":
            return make_response(f"Container {container_name} is not running (status: {container.status})", 400)

        # quick existence/executable checks (fast, won’t hang)
        for test_cmd, error_txt in [
            (f'test -f "{script_path}"', "Script not found"),
            (f'test -x "{script_path}"', "Script is not executable"),
        ]:
            rc, _ = container.exec_run(test_cmd)
            if rc != 0:
                return make_response(f"{error_txt}: {script_path}", 400)

        # If QGC is already running, bail early
        rc, out = container.exec_run('sh -lc \'pgrep -f "[Q]GroundControl"\'', user="gcs")
        if rc == 0 and out.strip():
            return make_response("QGroundControl already running", 200)

        # Launch in background & return immediately (no hang)
        # - write logs to /tmp/qgc.launch.log
        # - write pid to /tmp/qgc.pid
        launch_cmd = (
            f'sh -lc \'("{script_path}" >>/tmp/qgc.launch.log 2>&1 & echo $! > /tmp/qgc.pid) ; echo LAUNCHED\''
        )
        rc, out = container.exec_run(launch_cmd, user="gcs")
        if rc != 0 or b"LAUNCHED" not in out:
            return make_response("Failed to initiate launch", 400)

        # Optional: tiny bounded check loop (never hangs > LAUNCH_CHECK_SECS)
        deadline = time.monotonic() + LAUNCH_CHECK_SECS
        started = False
        while time.monotonic() < deadline:
            rc, out = container.exec_run('sh -lc \'pgrep -f "[Q]GroundControl"\'', user="gcs")
            if rc == 0 and out.strip():
                started = True
                break
            time.sleep(CHECK_INTERVAL)

        if started:
            return make_response("Success: QGroundControl launch initiated", 200)
        else:
            # still non-blocking: we return with 202 and a hint to check the log
            return make_response("Launch initiated, not yet observed running (check /tmp/qgc.launch.log)", 202)

    except NotFound:
        return make_response(f"Container not found: {container_name}", 400)
    except Exception as e:
        return make_response(f"Unexpected error: {str(e)}", 500)
