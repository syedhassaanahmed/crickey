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
SKIP_BASH = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or shutil.which("jq") is None,
    reason="Bash smoke script tests run on Unix CI with bash and jq",
)


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
printf '%s\n' "docker $*" >> "$state_dir/calls"
cmd=${{1:-}}
shift || true
http_id=httpcid
stdio_id=crickey-smoke-stdio-
case "$cmd" in
  image)
    if [[ " $* " == *' inspect '* ]]; then
      echo 'image inspect must not be used' >&2
      exit 1
    fi
    ;;
  create)
    printf '%s\n' "$*" > "$state_dir/create_args"
    echo "$http_id"
    echo false > "$state_dir/http_running"
    echo 0 > "$state_dir/http_exit"
    exit 0
    ;;
  start)
    if [[ "$scenario" == startup_crash || "$scenario" == fast_crash ]]; then
      echo false > "$state_dir/http_running"
      echo 2 > "$state_dir/http_exit"
    else
      echo true > "$state_dir/http_running"
    fi
    echo "$http_id"
    exit 0
    ;;
  port)
    if [[ "$scenario" == fast_crash ]]; then
      echo 'no public port' >&2
      exit 1
    fi
    echo '127.0.0.1:54321'
    exit 0
    ;;
  inspect)
    args="$*"
    if [[ "$args" == *State.Running* ]]; then
      cat "$state_dir/http_running" 2>/dev/null || echo false
    elif [[ "$args" == *State.ExitCode* ]]; then
      cat "$state_dir/http_exit" 2>/dev/null || echo 0
    fi
    exit 0
    ;;
  logs)
    if [[ "$scenario" == fast_crash ]]; then
      echo 'fast crash log'
    else
      echo 'stdout log'
      echo 'stderr log' >&2
    fi
    exit 0
    ;;
  stop)
    if [[ "$scenario" == stop_failure ]]; then
      exit 9
    fi
    echo false > "$state_dir/http_running"
    if [[ "$scenario" == stop_nonzero ]]; then
      echo 143 > "$state_dir/http_exit"
    else
      echo 0 > "$state_dir/http_exit"
    fi
    echo "$http_id"
    exit 0
    ;;
  rm)
    name=${{@: -1}}
    echo "$name" >> "$state_dir/removed"
    if [[ "$name" == "$http_id" ]]; then
      rm -f "$state_dir/http_running"
    fi
    exit 0
    ;;
  ps)
    if [[ " $* " == *"id=$http_id"* && -f "$state_dir/http_running" ]]; then
      echo "$http_id"
    fi
    exit 0
    ;;
  run)
    if [[ " $* " == *'--entrypoint id'* ]]; then
      if [[ "$scenario" == root_uid ]]; then
        echo 0
      else
        echo 10001
      fi
      exit 0
    fi
    printf '%s\n' "$*" > "$state_dir/stdio_args"
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
        tools='[{{"name":"better_than_player"}},{{"name":"find_player"}},{{"name":"leaderboard"}},{{"name":"player_record"}},{{"name":"query_stats"}}]'
        if [[ "$scenario" == wrong_tools ]]; then tools='[{{"name":"query_stats"}}]'; fi
        if [[ "$scenario" == four_tools ]]; then tools='[{{"name":"better_than_player"}},{{"name":"find_player"}},{{"name":"leaderboard"}},{{"name":"query_stats"}}]'; fi
        printf '{{"jsonrpc":"2.0","id":%s,"result":{{"tools":%s}}}}\n' "$id" "$tools"
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
output=''
data=''
url=''
headers_seen=''
while (($#)); do
  case "$1" in
    --dump-header) shift 2 ;;
    --output) output=$2; shift 2 ;;
    --data) data=$2; shift 2 ;;
    --write-out) shift 2 ;;
    --max-time|--request) shift 2 ;;
    --header) headers_seen+="$2|"; shift 2 ;;
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
case "$method" in
  tools/list)
    tools='[{"name":"better_than_player"},{"name":"find_player"},{"name":"leaderboard"},{"name":"player_record"},{"name":"query_stats"}]'
    if [[ "$scenario" == wrong_tools ]]; then tools='[{"name":"query_stats"}]'; fi
    if [[ "$scenario" == four_tools ]]; then tools='[{"name":"better_than_player"},{"name":"find_player"},{"name":"leaderboard"},{"name":"query_stats"}]'; fi
    printf 'data: {"jsonrpc":"2.0","id":%s,"result":{"tools":%s}}\n\n' "$id" "$tools" > "$output"
    ;;
  tools/call)
    link='https://stats.cricinfo.com/ci/engine/stats/index.html?class=2;orderby=hundreds;spanmin1=05+Jan+1971;spanmax1=04+Oct+2026;template=results;type=batting'
    is_error=false
    fetch=false
    rows='[]'
    if [[ "$scenario" == weak_link ]]; then link='https://stats.cricinfo.com/ci/engine/stats/index.html?class=2;spanmax1=04+Oct+2026;template=results;type=batting'; fi
    if [[ "$scenario" == missing_spanmax ]]; then link='https://stats.cricinfo.com/ci/engine/stats/index.html?class=2;spanmin1=05+Jan+1971;template=results;type=batting'; fi
    if [[ "$scenario" == is_error ]]; then is_error=true; fi
    if [[ "$scenario" == fetch_true ]]; then fetch=true; fi
    if [[ "$scenario" == rows_nonempty ]]; then rows='[{"Player":"A"}]'; fi
    printf '{"jsonrpc":"2.0","id":%s,"result":{"isError":%s,"structuredContent":{"link":"%s","fetch":%s,"total":null,"rows":%s}}}' "$id" "$is_error" "$link" "$fetch" "$rows" > "$output"
    if [[ "$scenario" == not_running_before_stop ]]; then echo false > "$SMOKE_STATE_DIR/http_running"; fi
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
    _write_executable(
        bin_dir / "date",
        f"""#!/usr/bin/env bash
state_dir={str(state_dir)!r}
if [[ ${{1:-}} == +%s%N && ${{SMOKE_SCENARIO:-}} == slow_stop ]]; then
  count_file="$state_dir/date_count"
  count=$(cat "$count_file" 2>/dev/null || echo 0)
  count=$((count + 1))
  echo "$count" > "$count_file"
  if (( count == 1 )); then echo 0; else echo 10000000000; fi
else
  /usr/bin/date "$@"
fi
""",
    )
    return bin_dir


@SKIP_BASH
def test_smoke_script_success_checks_docker_arguments(fake_bin: Path, tmp_path: Path) -> None:
    result = _run_script(fake_bin, tmp_path, "success")
    assert result.returncode == 0, result.stderr
    state = tmp_path / "state"
    calls = (state / "calls").read_text()
    assert "docker run --rm --entrypoint id crickey:fake -u" in calls
    assert "docker image inspect" not in calls
    assert "--read-only -p 127.0.0.1::8765 crickey:fake" in (state / "create_args").read_text()
    assert "--name crickey-smoke-stdio-" in (state / "stdio_args").read_text()
    assert "--read-only crickey:fake stdio" in (state / "stdio_args").read_text()
    removed = (state / "removed").read_text().splitlines()
    assert "httpcid" in removed
    assert any(name.startswith("crickey-smoke-stdio-") for name in removed)


@SKIP_BASH
def test_non_root_check_pulls_missing_local_image(fake_bin: Path, tmp_path: Path) -> None:
    result = _run_script(fake_bin, tmp_path, "registry_pull")
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "state" / "calls").read_text()
    assert "docker run --rm --entrypoint id crickey:fake -u" in calls
    assert "docker image inspect" not in calls


@SKIP_BASH
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("startup_crash", "HTTP container exited before Docker published a port"),
        ("fast_crash", "HTTP container exited before Docker published a port"),
        ("weak_link", "invalid structuredContent"),
        ("missing_spanmax", "invalid structuredContent"),
        ("is_error", "invalid structuredContent"),
        ("fetch_true", "invalid structuredContent"),
        ("rows_nonempty", "invalid structuredContent"),
        ("wrong_tools", "tool names were"),
        ("four_tools", "tool names were"),
        ("root_uid", "container runs as root"),
        ("slow_stop", "docker stop took"),
        ("not_running_before_stop", "not running before docker stop"),
        ("stop_failure", "docker stop failed"),
        ("stop_nonzero", "exited with code 143"),
        ("non_json_stdio", "stdio emitted non-JSON-RPC line"),
    ],
)
def test_smoke_script_failure_scenarios_remove_containers(
    fake_bin: Path, tmp_path: Path, scenario: str, expected: str
) -> None:
    result = _run_script(fake_bin, tmp_path, scenario)
    assert result.returncode == 1
    assert expected in result.stderr
    if scenario != "root_uid":
        removed = (tmp_path / "state" / "removed").read_text().splitlines()
        assert "httpcid" in removed
        if scenario == "non_json_stdio":
            assert any(name.startswith("crickey-smoke-stdio-") for name in removed)


@SKIP_BASH
@pytest.mark.parametrize(
    ("scenario", "needle", "replacement"),
    [
        (
            "four_tools",
            'readonly EXPECTED_TOOLS=\'["better_than_player","find_player","leaderboard","player_record","query_stats"]\'',
            'readonly EXPECTED_TOOLS=\'["better_than_player","find_player","leaderboard","query_stats"]\'',
        ),
        ("root_uid", "  assert_non_root\n", "  : # mutated non-root check removed\n"),
        (
            "slow_stop",
            "  (( stop_ms < STOP_THRESHOLD_MS )) || fail",
            "  true || fail",
        ),
        (
            "not_running_before_stop",
            '  container_running || fail "HTTP container was not running before docker stop; exit code $(container_exit_code)"',
            '  true || fail "HTTP container was not running before docker stop; exit code $(container_exit_code)"',
        ),
        ("is_error", ".result.isError == false and", "true and"),
        ("fetch_true", ".result.structuredContent.fetch == false and", "true and"),
        ("rows_nonempty", ".result.structuredContent.rows == []", "true"),
        (
            "missing_spanmax",
            '(.result.structuredContent.link | contains("spanmax1=")) and',
            "true and",
        ),
    ],
)
def test_review_mutations_make_bad_scenarios_pass(
    fake_bin: Path, tmp_path: Path, scenario: str, needle: str, replacement: str
) -> None:
    mutated = tmp_path / "mutated_smoke.sh"
    text = SCRIPT.read_text()
    assert needle in text
    mutated.write_text(text.replace(needle, replacement))
    mutated.chmod(mutated.stat().st_mode | stat.S_IXUSR)

    result = _run_script(fake_bin, tmp_path, scenario, script=mutated)

    assert result.returncode == 0, result.stderr


@SKIP_BASH
def test_review_mutation_dropping_stdio_rm_is_caught(fake_bin: Path, tmp_path: Path) -> None:
    mutated = tmp_path / "mutated_smoke.sh"
    needle = '  run_docker rm -f "$stdio_container_name" >/dev/null 2>&1 || true\n'
    text = SCRIPT.read_text()
    assert needle in text
    mutated.write_text(text.replace(needle, ""))
    mutated.chmod(mutated.stat().st_mode | stat.S_IXUSR)

    result = _run_script(fake_bin, tmp_path, "success", script=mutated)

    assert result.returncode == 0, result.stderr
    removed = (tmp_path / "state" / "removed").read_text().splitlines()
    assert not any(name.startswith("crickey-smoke-stdio-") for name in removed)


@SKIP_BASH
def test_review_mutation_weakening_create_args_is_caught(fake_bin: Path, tmp_path: Path) -> None:
    mutated = tmp_path / "mutated_smoke.sh"
    needle = 'run_docker create --read-only -p 127.0.0.1::8765 "$image"'
    text = SCRIPT.read_text()
    assert needle in text
    mutated.write_text(text.replace(needle, 'run_docker create -p 0.0.0.0::8765 "$image"'))
    mutated.chmod(mutated.stat().st_mode | stat.S_IXUSR)

    result = _run_script(fake_bin, tmp_path, "success", script=mutated)

    assert result.returncode == 0, result.stderr
    create_args = (tmp_path / "state" / "create_args").read_text()
    assert "--read-only" not in create_args
    assert "0.0.0.0::8765" in create_args


def _run_script(
    fake_bin: Path, tmp_path: Path, scenario: str, script: Path = SCRIPT
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["SMOKE_SCENARIO"] = scenario
    env["SMOKE_STATE_DIR"] = str(tmp_path / "state")
    return subprocess.run(
        ["bash", str(script), "crickey:fake"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
