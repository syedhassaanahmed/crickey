#!/usr/bin/env bash
# Requirements: bash 4+, docker, curl, jq, timeout.
set -Eeuo pipefail

readonly EXPECTED_TOOLS='["better_than_player","find_player","leaderboard","player_record","query_stats"]'
readonly HEALTH_BODY='{"status":"ok"}'
readonly PROTOCOL_VERSION='2026-07-28'
readonly STOP_GRACE_SECONDS=10
readonly STOP_THRESHOLD_MS=9750
readonly HTTP_READY_TIMEOUT_SECONDS=30
readonly CURL_MAX_TIME=10
readonly DOCKER_TIMEOUT_SECONDS=30
readonly STOP_COMMAND_TIMEOUT_SECONDS=20

image=${1:-}
container_id=""
stdio_container_name=""
scratch_dir=""
failure_message=""
reported_failure=0
curl_seq=0
stop_ms=0
RPC_BODY=""
RPC_RESPONSE=""
ALLOW_STDIO_METHOD_NOT_FOUND=0
STDIO_RESPONSE=""

usage() {
  printf 'usage: bash scripts/smoke_test_image.sh <image>\n' >&2
}

record_failure() {
  if [[ -z "$failure_message" ]]; then
    failure_message=$1
  fi
}

on_error() {
  local status=$?
  local command=${BASH_COMMAND:-unknown}
  if (( status != 0 )) && [[ -z "$failure_message" ]]; then
    failure_message="command failed with exit ${status}: ${command}"
  fi
}

cleanup() {
  local status=$?
  trap - EXIT ERR INT TERM
  if (( status != 0 )); then
    reported_failure=1
    printf 'FAIL: %s\n' "${failure_message:-unexpected smoke test failure}" >&2
    if [[ -n "$container_id" ]]; then
      printf 'Container logs (tail 50):\n' >&2
      timeout "$DOCKER_TIMEOUT_SECONDS" docker logs --tail 50 "$container_id" >&2 || true
    fi
  fi
  if [[ -n "$container_id" ]]; then
    timeout "$DOCKER_TIMEOUT_SECONDS" docker rm -f "$container_id" >/dev/null 2>&1 || true
  fi
  if [[ -n "$stdio_container_name" ]]; then
    timeout "$DOCKER_TIMEOUT_SECONDS" docker rm -f "$stdio_container_name" >/dev/null 2>&1 || true
  fi
  if [[ -n "$scratch_dir" ]]; then
    rm -rf "$scratch_dir"
  fi
  if (( status == 0 )) && (( reported_failure == 0 )); then
    printf 'PASS: HTTP and stdio smoke tests passed for %s\n' "$image"
    printf 'STOP_SECONDS=%d.%03d\n' "$((stop_ms / 1000))" "$((stop_ms % 1000))"
  fi
  exit "$status"
}

trap on_error ERR
trap cleanup EXIT
trap 'record_failure "interrupted"; exit 130' INT TERM

fail() {
  record_failure "$1"
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

run_docker() {
  timeout "$DOCKER_TIMEOUT_SECONDS" docker "$@"
}

run_docker_stop() {
  timeout "$STOP_COMMAND_TIMEOUT_SECONDS" docker stop --time "$STOP_GRACE_SECONDS" "$container_id"
}

assert_non_root() {
  local uid
  uid=$(run_docker run --rm --entrypoint id "$image" -u) || fail "docker run id -u failed"
  uid=${uid%%:*}
  if [[ "$uid" == "0" ]]; then
    fail "container runs as root (id -u returned 0)"
  fi
  [[ "$uid" =~ ^[0-9]+$ ]] || fail "could not parse container uid from id -u output: ${uid}"
}

create_and_start_http_container() {
  container_id=$(run_docker create --read-only -p 127.0.0.1::8765 "$image") || fail "docker create failed"
  [[ -n "$container_id" ]] || fail "docker create did not return a container id"
  local start_output
  if ! start_output=$(run_docker start "$container_id" 2>&1); then
    fail "docker start failed: ${start_output}"
  fi
}

container_running() {
  [[ $(run_docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null || true) == "true" ]]
}

container_exit_code() {
  run_docker inspect -f '{{.State.ExitCode}}' "$container_id" 2>/dev/null || printf '<unknown>'
}

HTTP_PORT=""

read_http_port() {
  local mapped port
  if ! container_running; then
    fail "HTTP container exited before Docker published a port; exit code $(container_exit_code)"
  fi
  mapped=$(run_docker port "$container_id" 8765/tcp) || return 1
  port=${mapped##*:}
  [[ "$port" =~ ^[0-9]+$ ]] || return 1
  HTTP_PORT=$port
}

wait_for_health() {
  local port=$1 deadline now body
  deadline=$((SECONDS + HTTP_READY_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if ! container_running; then
      fail "HTTP container exited before /health became ready; exit code $(container_exit_code)"
    fi
    body=$(curl --silent --show-error --max-time 2 "http://127.0.0.1:${port}/health" 2>/dev/null || true)
    if [[ "$body" == "$HEALTH_BODY" ]]; then
      return
    fi
    sleep 0.2
  done
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  fail "/health did not become ready by ${now}; last body: ${body:-<none>}"
}

new_scratch_files() {
  curl_seq=$((curl_seq + 1))
  printf '%s/headers.%s %s/body.%s\n' "$scratch_dir" "$curl_seq" "$scratch_dir" "$curl_seq"
}

curl_rpc() {
  local port=$1 method=$2 name=$3 payload=$4 headers body status
  read -r headers body < <(new_scratch_files)
  local curl_args=(
    --silent --show-error --max-time "$CURL_MAX_TIME"
    --request POST
    --dump-header "$headers"
    --output "$body"
    --header 'Accept: application/json, text/event-stream'
    --header 'Content-Type: application/json'
    --header "Mcp-Protocol-Version: ${PROTOCOL_VERSION}"
    --header "Mcp-Method: ${method}"
  )
  if [[ -n "$name" ]]; then
    curl_args+=(--header "Mcp-Name: ${name}")
  fi
  status=$(curl "${curl_args[@]}" --data "$payload" "http://127.0.0.1:${port}/mcp" --write-out '%{http_code}') \
    || fail "curl ${method} failed"
  if [[ "$status" != 2* ]]; then
    RPC_BODY=$(cat "$body")
    fail "curl ${method} returned HTTP ${status}: ${RPC_BODY}"
  fi
  RPC_BODY=$(cat "$body")
}

json_messages() {
  local body=$1
  if jq -e . >/dev/null 2>&1 <<<"$body"; then
    jq -c . <<<"$body"
  else
    while IFS= read -r line; do
      line=${line%$'\r'}
      [[ "$line" == data:* ]] || continue
      line=${line#data:}
      line=${line# }
      [[ -n "$line" && "$line" != '[DONE]' ]] || continue
      jq -c . >/dev/null 2>&1 <<<"$line" && printf '%s\n' "$line"
    done <<<"$body"
  fi
}

rpc_result() {
  local body=$1 id=$2 result
  result=$(json_messages "$body" | jq -c --argjson id "$id" 'select(.id == $id)' | tail -n 1 || true)
  [[ -n "$result" ]] || fail "no JSON-RPC response with id ${id}: ${body}"
  if jq -e '.error?' >/dev/null <<<"$result"; then
    fail "JSON-RPC id ${id} returned error: ${result}"
  fi
  RPC_RESPONSE=$result
}

assert_tools_json() {
  local json=$1 found
  found=$(jq -c '[.result.tools[].name] | sort' <<<"$json") || fail "tools/list response did not contain tools: ${json}"
  if [[ "$found" != "$EXPECTED_TOOLS" ]]; then
    fail "tool names were ${found}, expected ${EXPECTED_TOOLS}"
  fi
}

mcp_meta() {
  jq -c -n '{
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "crickey-smoke-test", "version": "0.1.0"}
  }'
}

check_http_mcp() {
  local port=$1 response meta payload
  meta=$(mcp_meta)
  payload=$(jq -c -n --argjson meta "$meta" '{
    jsonrpc: "2.0",
    id: 2,
    method: "tools/list",
    params: {_meta: $meta}
  }')
  curl_rpc "$port" tools/list "" "$payload"
  rpc_result "$RPC_BODY" 2
  response=$RPC_RESPONSE
  assert_tools_json "$response"

  payload=$(jq -c -n --argjson meta "$meta" '{
    jsonrpc: "2.0",
    id: 3,
    method: "tools/call",
    params: {
      name: "query_stats",
      arguments: {
        query: {class: 2, type: "batting", orderby: "hundreds"},
        fetch: false
      },
      _meta: $meta
    }
  }')
  curl_rpc "$port" tools/call query_stats "$payload"
  rpc_result "$RPC_BODY" 3
  response=$RPC_RESPONSE
  jq -e '
    .result.isError == false and
    (.result.structuredContent | has("link")) and
    (.result.structuredContent.link | startswith("https://stats.cricinfo.com/")) and
    (.result.structuredContent.link | contains("spanmin1=")) and
    (.result.structuredContent.link | contains("spanmax1=")) and
    (.result.structuredContent | has("fetch")) and
    .result.structuredContent.fetch == false and
    (.result.structuredContent | has("total")) and
    .result.structuredContent.total == null and
    (.result.structuredContent | has("rows")) and
    .result.structuredContent.rows == []
  ' >/dev/null <<<"$response" || fail "query_stats(fetch=false) returned invalid structuredContent: ${response}"
}

stop_http_container() {
  local start_ns end_ns exit_code
  container_running || fail "HTTP container was not running before docker stop; exit code $(container_exit_code)"
  start_ns=$(date +%s%N)
  run_docker_stop >/dev/null || fail "docker stop failed"
  end_ns=$(date +%s%N)
  stop_ms=$(((end_ns - start_ns) / 1000000))
  exit_code=$(container_exit_code)
  [[ "$exit_code" == "0" ]] || fail "HTTP container exited with code ${exit_code} after docker stop"
  (( stop_ms < STOP_THRESHOLD_MS )) || fail "docker stop took $((stop_ms / 1000)).$((stop_ms % 1000))s, the full ${STOP_GRACE_SECONDS}s grace period"
}

stdio_send() {
  printf '%s\n' "$1" >&"${STDIO[1]}"
}

stdio_read_response() {
  local wanted_id=$1 line
  while IFS= read -r -t 10 line <&"${STDIO[0]}"; do
    if ! jq -e 'type == "object" and .jsonrpc == "2.0"' >/dev/null 2>&1 <<<"$line"; then
      fail "stdio emitted non-JSON-RPC line: ${line}"
    fi
    if jq -e --argjson id "$wanted_id" '.id == $id' >/dev/null <<<"$line"; then
      if jq -e '.error?' >/dev/null <<<"$line"; then
        if (( ALLOW_STDIO_METHOD_NOT_FOUND == 1 )) && jq -e '.error.code == -32601' >/dev/null <<<"$line"; then
          STDIO_RESPONSE=$line
          return
        fi
        fail "stdio JSON-RPC id ${wanted_id} returned error: ${line}"
      fi
      STDIO_RESPONSE=$line
      return
    fi
  done
  fail "timed out waiting for stdio response id ${wanted_id}"
}

check_stdio() {
  local response status payload
  stdio_container_name="crickey-smoke-stdio-$$"
  coproc STDIO { timeout 20 docker run --name "$stdio_container_name" -i --rm --read-only "$image" stdio; }
  payload=$(jq -c -n '{
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: "2026-07-28",
      capabilities: {},
      clientInfo: {name: "crickey-smoke-test", version: "0.1.0"}
    }
  }')
  stdio_send "$payload"
  ALLOW_STDIO_METHOD_NOT_FOUND=1
  stdio_read_response 1
  ALLOW_STDIO_METHOD_NOT_FOUND=0
  payload=$(jq -c -n '{jsonrpc: "2.0", method: "notifications/initialized", params: {}}')
  stdio_send "$payload"
  payload=$(jq -c -n '{jsonrpc: "2.0", id: 2, method: "tools/list", params: {}}')
  stdio_send "$payload"
  stdio_read_response 2
  response=$STDIO_RESPONSE
  assert_tools_json "$response"
  local stdio_stdin=${STDIO[1]}
  exec {stdio_stdin}>&-
  set +e
  wait "$STDIO_PID"
  status=$?
  set -e
  if (( status != 0 )); then
    fail "stdio docker run exited with status ${status}"
  fi
  local remaining
  remaining=$(run_docker ps -a --filter "name=${stdio_container_name}" --format '{{.Names}}' || true)
  [[ -z "$remaining" ]] || fail "stdio container ${stdio_container_name} was left behind"
  run_docker rm -f "$stdio_container_name" >/dev/null 2>&1 || true
  stdio_container_name=""
}

check_no_leftover_container() {
  if [[ -n "$container_id" ]]; then
    local remaining
    remaining=$(run_docker ps -a --filter "id=${container_id}" --format '{{.ID}}' || true)
    [[ -z "$remaining" ]] || fail "container ${container_id} was left behind"
  fi
}

main() {
  [[ -n "$image" ]] || { usage; exit 2; }
  require_command docker
  require_command curl
  require_command jq
  require_command timeout
  scratch_dir=".local/smoke-test-$$"
  mkdir -p "$scratch_dir"
  assert_non_root
  create_and_start_http_container
  read_http_port || fail "could not read published HTTP port"
  wait_for_health "$HTTP_PORT"
  check_http_mcp "$HTTP_PORT"
  stop_http_container
  run_docker rm -f "$container_id" >/dev/null
  check_no_leftover_container
  container_id=""
  check_stdio
}

main "$@"
