# ruff: noqa: E501
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke_test_image.sh"
SENTENCE = (
    "crickey is an MCP server that answers cricket statistics questions from Cricinfo Statsguru."
)
OLD_DISCLAIMER = "crickey is for personal use at the user's own risk"


def test_image_description_labels_are_short_description() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert dockerfile.count(SENTENCE) == 1
    assert release.count(SENTENCE) == 2
    assert OLD_DISCLAIMER not in dockerfile
    assert OLD_DISCLAIMER not in release


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    bin_dir.mkdir()
    state_dir.mkdir()
    _write_executable(
        bin_dir / "docker",
        f"""#!/usr/bin/env bash
set -euo pipefail
state_dir={str(state_dir)!r}
scenario=${{SMOKE_SCENARIO:-success}}
mkdir -p "$state_dir"
cmd=${{1:-}}
shift || true
case "$cmd" in
  image)
    if [[ ${{1:-}} == inspect ]]; then
      echo '10001:10001'
      exit 0
    fi
    ;;
  create)
    echo cid
    echo false > "$state_dir/running"
    echo 0 > "$state_dir/exit_code"
    exit 0
    ;;
  start)
    if [[ "$scenario" == startup_crash ]]; then
      echo false > "$state_dir/running"
      echo 2 > "$state_dir/exit_code"
    else
      echo true > "$state_dir/running"
    fi
    echo cid
    exit 0
    ;;
  port)
    echo '127.0.0.1:54321'
    exit 0
    ;;
  inspect)
    fmt=""
    while (($#)); do
      if [[ $1 == -f ]]; then
        fmt=$2
        shift 2
      else
        shift
      fi
    done
    if [[ "$fmt" == '{{.State.Running}}' ]]; then
      cat "$state_dir/running"
    elif [[ "$fmt" == '{{.State.ExitCode}}' ]]; then
      cat "$state_dir/exit_code"
    fi
    exit 0
    ;;
  logs)
    echo 'stdout log'
    echo 'stderr log' >&2
    exit 0
    ;;
  stop)
    if [[ "$scenario" == stop_failure ]]; then
      exit 9
    fi
    echo false > "$state_dir/running"
    if [[ "$scenario" == stop_nonzero ]]; then
      echo 143 > "$state_dir/exit_code"
    else
      echo 0 > "$state_dir/exit_code"
    fi
    echo cid
    exit 0
    ;;
  rm)
    echo removed > "$state_dir/removed"
    rm -f "$state_dir/running"
    exit 0
    ;;
  ps)
    if [[ -f "$state_dir/running" ]]; then
      echo cid
    fi
    exit 0
    ;;
  run)
    if [[ "$*" != *' stdio' ]]; then
      exit 0
    fi
    if [[ "$scenario" == non_json_stdio ]]; then
      echo 'banner on stdout'
      exit 0
    fi
    while IFS= read -r line; do
      method=$(jq -r '.method // empty' <<<"$line")
      id=$(jq -r '.id // empty' <<<"$line")
      if [[ "$method" == initialize ]]; then
        printf '{{"jsonrpc":"2.0","id":%s,"result":{{"protocolVersion":"2026-07-28","capabilities":{{}},"serverInfo":{{"name":"crickey","version":"0.1.0"}}}}}}\n' "$id"
      elif [[ "$method" == tools/list ]]; then
        if [[ "$scenario" == wrong_tools ]]; then
          printf '{{"jsonrpc":"2.0","id":%s,"result":{{"tools":[{{"name":"query_stats"}}]}}}}\n' "$id"
        else
          printf '{{"jsonrpc":"2.0","id":%s,"result":{{"tools":[{{"name":"better_than_player"}},{{"name":"find_player"}},{{"name":"leaderboard"}},{{"name":"player_record"}},{{"name":"query_stats"}}]}}}}\n' "$id"
        fi
      fi
    done
    exit 0
    ;;
esac
echo "unexpected docker call: $cmd $*" >&2
exit 64
""",
    )
    _write_executable(
        bin_dir / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
scenario=${SMOKE_SCENARIO:-success}
headers=''
output=''
data=''
url=''
while (($#)); do
  case "$1" in
    --dump-header) headers=$2; shift 2 ;;
    --output) output=$2; shift 2 ;;
    --data) data=$2; shift 2 ;;
    --write-out) shift 2 ;;
    --max-time|--request|--header) shift 2 ;;
    --silent|--show-error) shift ;;
    *) url=$1; shift ;;
  esac
done
if [[ "$url" == */health ]]; then
  printf '{"status":"ok"}'
  exit 0
fi
method=$(jq -r '.method // empty' <<<"$data")
id=$(jq -r '.id // empty' <<<"$data")
if [[ -n "$headers" ]]; then
  printf 'HTTP/1.1 200 OK\r\n' > "$headers"
  if [[ "$method" == initialize ]]; then
    printf 'Mcp-Session-Id: sid\r\n' >> "$headers"
  fi
  printf '\r\n' >> "$headers"
fi
case "$method" in
  initialize)
    printf '{"jsonrpc":"2.0","id":%s,"result":{"protocolVersion":"2026-07-28","capabilities":{},"serverInfo":{"name":"crickey","version":"0.1.0"}}}' "$id" > "$output"
    ;;
  notifications/initialized)
    : > "$output"
    ;;
  tools/list)
    if [[ "$scenario" == wrong_tools ]]; then
      printf '{"jsonrpc":"2.0","id":%s,"result":{"tools":[{"name":"query_stats"}]}}' "$id" > "$output"
    else
      printf 'data: {"jsonrpc":"2.0","id":%s,"result":{"tools":[{"name":"better_than_player"},{"name":"find_player"},{"name":"leaderboard"},{"name":"player_record"},{"name":"query_stats"}]}}\n\n' "$id" > "$output"
    fi
    ;;
  tools/call)
    link='https://stats.cricinfo.com/ci/engine/stats/index.html?class=2;orderby=hundreds;spanmin1=05+Jan+1971;spanmax1=04+Oct+2026;template=results;type=batting'
    if [[ "$scenario" == weak_link ]]; then
      link='https://stats.cricinfo.com/ci/engine/stats/index.html?class=2;spanmax1=04+Oct+2026;template=results;type=batting'
    fi
    printf '{"jsonrpc":"2.0","id":%s,"result":{"isError":false,"structuredContent":{"link":"%s","fetch":false,"total":null,"rows":[]}}}' "$id" "$link" > "$output"
    ;;
  *)
    printf '{"jsonrpc":"2.0","id":%s,"error":{"code":-32601,"message":"unknown"}}' "$id" > "$output"
    ;;
esac
printf '200'
""",
    )
    _write_executable(
        bin_dir / "timeout",
        """#!/usr/bin/env bash
shift
exec "$@"
""",
    )
    return bin_dir


@pytest.mark.skipif(sys.platform == "win32", reason="Bash smoke script tests run on Unix CI")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required")
def test_smoke_script_success(fake_bin: Path, tmp_path: Path) -> None:
    result = _run_script(fake_bin, tmp_path, "success")

    assert result.returncode == 0, result.stderr
    assert "PASS:" in result.stdout
    assert (tmp_path / "state" / "removed").read_text() == "removed\n"


@pytest.mark.skipif(sys.platform == "win32", reason="Bash smoke script tests run on Unix CI")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required")
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("startup_crash", "HTTP container exited before /health"),
        ("weak_link", "invalid structuredContent"),
        ("wrong_tools", "tool names were"),
        ("stop_nonzero", "exited with code 143"),
        ("non_json_stdio", "stdio emitted non-JSON-RPC line"),
    ],
)
def test_smoke_script_failures_remove_container(
    fake_bin: Path, tmp_path: Path, scenario: str, expected: str
) -> None:
    result = _run_script(fake_bin, tmp_path, scenario)

    assert result.returncode == 1
    assert expected in result.stderr
    if scenario != "non_json_stdio":
        assert "stdout log" in result.stderr
        assert "stderr log" in result.stderr
    assert (tmp_path / "state" / "removed").read_text() == "removed\n"


def _run_script(fake_bin: Path, tmp_path: Path, scenario: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["SMOKE_SCENARIO"] = scenario
    return subprocess.run(
        ["bash", str(SCRIPT), "crickey:fake"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
