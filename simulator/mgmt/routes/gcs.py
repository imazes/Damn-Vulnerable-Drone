from flask import make_response
from docker.errors import NotFound
from . import bp
from .utils import get_container
from shlex import quote

TIMEOUT_SECS = 6

@bp.route("/qgc", methods=["POST"])
def open_qgc():
    container_name = "ground-control-station"
    script_path = "/usr/local/bin/launch_qgc.sh"

    try:
        container = get_container(container_name)
        if container.status != "running":
            return make_response(f"Container {container_name} is not running (status: {container.status})", 400)

        # quick checks
        for test_cmd, error_txt in [
            (f'test -f "{script_path}"', "Script not found"),
            (f'test -x "{script_path}"', "Script is not executable"),
        ]:
            rc, _ = container.exec_run(test_cmd)
            if rc != 0:
                return make_response(f"{error_txt}: {script_path}", 400)

        # Run with a 6s timeout (prefers GNU coreutils 'timeout', then busybox timeout,
        # else manual kill after 6s). Return 124 when we enforced the timeout.
        cmd = (
            "sh -lc '"
            "if command -v timeout >/dev/null 2>&1; then "
            f"  timeout -k 1s {TIMEOUT_SECS}s {quote(script_path)}; "
            "elif command -v busybox >/dev/null 2>&1; then "
            f"  busybox timeout -t {TIMEOUT_SECS} {quote(script_path)}; "
            "else "
            f"  ({quote(script_path)} & pid=$!; "
            f"   (sleep {TIMEOUT_SECS}; kill -TERM $pid 2>/dev/null; sleep 1; kill -KILL $pid 2>/dev/null; exit 124) & "
            "   wait $pid"
            "  ); "
            "fi'"
        )

        exec_result = container.exec_run(cmd, user="gcs")
        output = (exec_result.output or b"").decode(errors="ignore").strip()
        code = exec_result.exit_code

        if code == 0:
            return make_response(f"Success:\n{output}", 200)
        elif code == 124:
            # 124 is the standard timeout exit code
            return make_response("Timed out after 6s while launching QGroundControl.", 408)
        else:
            return make_response(
                f"Command failed with exit code {code}:\n{output}",
                400,
            )

    except NotFound:
        return make_response(f"Container not found: {container_name}", 400)
    except Exception as e:
        return make_response(f"Unexpected error: {str(e)}", 500)
