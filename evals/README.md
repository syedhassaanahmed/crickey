# Evals

Language models answer crickey's golden questions through crickey, and deterministic checks score each answer (#18). Use the scores to see which models can use crickey, to catch regressions, and to find where tool descriptions trip small models up (D13, D14).

[golden_questions.toml](golden_questions.toml) is the one place the golden questions are listed (D41). Each has its general form, and the case the eval asks: real players filled in, the calls it needs and its known answer, which can't change (D37). Each also has a held-out case, the same kind of question in other words about other players, teams, formats or periods, which nothing in crickey quotes (D45). The questions ask live Statsguru through a running crickey, so a run follows D7–D12: one request every 15 seconds, and a block stops the run. [docs/plan.md › Evals](../docs/plan.md#evals) has the design, and [docs/research.md › 17](../docs/research.md#17-evals-with-inspect-ai-and-ollama) the facts behind it.

## Run it
The steps are the same on Windows, Linux (including WSL) and macOS. Run the commands from the repository root, in Bash, zsh or PowerShell; where they differ, both forms are shown.

1. **Start crickey** in another terminal, with `uv run crickey serve` or the Docker image ([README](../README.md)).
2. **Start Ollama** in another terminal, with the eval's settings. Quit the Ollama app first if it's running, from its menu bar or tray icon (on Linux, `sudo systemctl stop ollama`), because it holds Ollama's port.
   ```shell
   OLLAMA_CONTEXT_LENGTH=65536 LLAMA_ARG_CACHE_RAM=0 ollama serve
   ```
   In PowerShell:
   ```powershell
   $env:OLLAMA_CONTEXT_LENGTH="65536"; $env:LLAMA_ARG_CACHE_RAM="0"; ollama serve
   ```
   - `OLLAMA_CONTEXT_LENGTH` gives models a 64k-token context. They read about 8,000 tokens of tool definitions and instructions before each question, and Ollama's own default depends on the GPU's memory, from 4k to 256k. `32768` also works, in less memory.
   - `LLAMA_ARG_CACHE_RAM=0` stops Ollama keeping up to 8 GiB of earlier conversations.
   - Leave the cache type and flash attention at their defaults. An 8-bit cache reads prompts about 4.6 times slower on a CPU.
3. **Pull a model** that calls tools, in a third terminal:
   ```shell
   ollama pull qwen3.8:27b
   ```
   `qwen3.8:27b` does best in the evals: over 3 epochs, it passed every check in 27 of 30 golden answers and 27 of 30 held-out ones. It thinks before each reply, but briefly, and at a 64k context it holds about 26 GiB of GPU memory, and up to 28.5 (R17). With less memory, or no GPU, use `qwen3:4b-instruct`, which passed 15 and 8; `qwen3:4b` always thinks first, which takes minutes on a CPU.
4. **Run the eval:**
   ```shell
   uv run --group evals --env-file evals/inspect.env inspect eval evals/golden_questions.py --model ollama/qwen3.8:27b
   ```
   `evals/inspect.env` writes JSON logs to `evals/logs`, and gives each model request an hour, because Inspect's HTTP clients give up after 10 minutes and a CPU can take longer than that to read the first prompt.

   Keep the machine awake while it runs, because sleep freezes a reply midway: on macOS, put `caffeinate -i` in front of the command, which keeps the Mac awake until the eval ends (R17). Closing the lid still sleeps.

If Ollama runs on another machine, or outside WSL while the eval runs inside it, set `OLLAMA_BASE_URL` to its address, such as `http://<host>:11434/v1`, and add `OLLAMA_HOST` to the command that starts Ollama, so that it listens there. To keep the Ollama app running instead, start the eval's Ollama on another port, with `OLLAMA_HOST=127.0.0.1:11435` in the command, and set `OLLAMA_BASE_URL=http://127.0.0.1:11435/v1` for the eval.

`--group evals` adds the eval's tools (the `evals` dependency group, D39), which crickey's own `uv sync` leaves out. On Python 3.15, two of them, zstandard and PyYAML, have no wheels yet, so uv builds them from source, which needs a C compiler: gcc or clang on Linux and macOS, or [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) on Windows (R17). Without Build Tools, the eval also runs in a Linux container, with crickey inside it (R17). The run asks each question three times (epochs), one question at a time.

## Options
- `--epochs 1` asks each question once. On a CPU, the model can read for 5 to 10 minutes before it starts on the first question; later questions reuse that reading and take a few minutes each.
- `--max-tokens 2048` caps each reply. Without it, a small model can write a single reply for half an hour or more (R17).
- `--sample-id q5-tendulkar-odi-world-cup-hundreds` asks one question; `golden_questions.toml` has the IDs.
- `-T crickey_url=http://127.0.0.1:8766/mcp` uses a crickey on another port.
- Ctrl+C stops a run and keeps the questions it finished in the log; `inspect eval-retry <log>`, with the same `uv run` prefix, asks the rest.
- `inspect ctl sample cancel golden_questions <sample id> <epoch> --action score`, from another terminal, ends a reply that runs away and scores what the model wrote so far (R17).
- `--model` takes any of [Inspect's model providers](https://inspect.aisi.org.uk/providers.html), for example `openai/<model>` or `anthropic/<model>` with an API key in the environment. Several models run one after another and share crickey's cache, so only the first pays for the Statsguru requests (D17).

## Results
`uv run --group evals --env-file evals/inspect.env inspect view` opens the logs: each question's conversation, tool calls and scores. The logs hold Statsguru's figures, so `evals/logs/` is gitignored and logs are never committed (D25).

Each question gets these scores, averaged over its epochs. Each check reports the golden cases (`golden`) apart from the held-out ones (`held_out`):

| Score | Passes when |
|---|---|
| `all_checks` | Every check below passes. A model's headline score is its golden mean. |
| `known_answer` | The final answer contains every fact in the case's known answer (R10), outside its links. |
| `answer_tool` | The answer tool the question needs was called and answered with a Statsguru query link, rather than only `query_stats`. A clarification doesn't count. |
| `arguments` | Each call the question needs was made, with the format, player, discipline, metrics, filters, period, minimum, split and number of teams that decide its answer. A player named rather than given by ID counts once crickey resolves the name to them. |
| `proof` | The answer cites a Statsguru query link, not just a profile link, and every link it cites came from a tool result (D16). |
| `grounding` | Every figure in the answer appears in a tool result or in the question (D13). |
| `cost` | Not a check: tool calls, tool errors, Statsguru requests and cached pages (from each result's `_meta`, D38), tokens and seconds. |

## Adding or changing a golden question
Edit `golden_questions.toml`; nothing else lists the golden questions. Give a new golden question a held-out case too. Check each case's known answer live with crickey's tools, within D7–D12, and record it in R10.

Never copy a case's wording, players or values into crickey's tool descriptions, parameter descriptions or server instructions, and check a change made for a failing case on the held-out cases too (D45).
