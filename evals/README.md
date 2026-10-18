# Evals

Language models answer crickey's golden questions through crickey, and deterministic checks score each answer (#18). Use the scores to see which models can use crickey, to catch regressions, and to find where tool descriptions trip small models up (D13, D14).

[golden_questions.toml](golden_questions.toml) is the one place the golden questions are listed (D41). Each has its general form, and the case the eval asks: real players filled in, the calls it needs and its known answer, which can't change (D37). The questions ask live Statsguru through a running crickey, so a run follows D7–D12: one request every 15 seconds, and a block stops the run. [docs/plan.md › Evals](../docs/plan.md#evals) has the design, and [docs/research.md › 17](../docs/research.md#17-evals-with-inspect-ai-and-ollama) the facts behind it.

## Run it
The steps are the same on Windows, Linux (including WSL) and macOS, and the commands work in Bash, zsh and PowerShell. Run them from the repository root.

1. **Start crickey** in another terminal, with `uv run crickey serve` or the Docker image ([README](../README.md)).
2. **Set up Ollama:**
   - Pull a model that calls tools and doesn't think, such as `qwen3:4b-instruct`:
     ```shell
     ollama pull qwen3:4b-instruct
     ```
     `qwen3:4b` always thinks first, which takes minutes on a CPU.
   - Give the Ollama server these settings, then restart it. [Ollama's FAQ](https://docs.ollama.com/faq#how-do-i-configure-ollama-server) shows how to set its environment variables on each system; the Ollama app on Windows and macOS also has a context-length slider in its settings.
     - `OLLAMA_CONTEXT_LENGTH` of at least `32768`, or `65536` with memory to spare. A model reads about 8,000 tokens of tool definitions and instructions before each question.
     - `LLAMA_ARG_CACHE_RAM=0`. Otherwise Ollama keeps up to 8 GiB of earlier conversations.
     - Leave the cache type and flash attention at their defaults. An 8-bit cache reads prompts about 4.6 times slower on a CPU.
   - If Ollama runs on another machine, or outside WSL while the eval runs inside it, set `OLLAMA_BASE_URL` to its address, such as `http://<host>:11434/v1`. The Ollama server must listen on that address (`OLLAMA_HOST`, in the same FAQ).
3. **Run the eval:**
   ```shell
   uv run --group evals --env-file evals/inspect.env inspect eval evals/golden_questions.py --model ollama/qwen3:4b-instruct
   ```
   `evals/inspect.env` writes JSON logs to `evals/logs`, and gives each model request an hour, because Inspect's HTTP clients give up after 10 minutes and a CPU can take longer than that to read the first prompt.

`--group evals` adds the eval's tools (the `evals` dependency group, D39), which crickey's own `uv sync` leaves out. On Python 3.15, two of them, zstandard and PyYAML, have no wheels yet, so uv builds them from source, which needs a C compiler: gcc or clang on Linux and macOS, or [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) on Windows (R17). The run asks each question three times (epochs), one question at a time.

## Options
- `--epochs 1` asks each question once. On a CPU, the model can read for 5 to 10 minutes before it starts on the first question; later questions reuse that reading and take a few minutes each.
- `--sample-id q5-tendulkar-odi-world-cup-hundreds` asks one question; `golden_questions.toml` has the IDs.
- `-T crickey_url=http://127.0.0.1:8766/mcp` uses a crickey on another port.
- `--model` takes any of [Inspect's model providers](https://inspect.aisi.org.uk/providers.html), for example `openai/<model>` or `anthropic/<model>` with an API key in the environment. Several models run one after another and share crickey's cache, so only the first pays for the Statsguru requests (D17).

## Results
`uv run --group evals --env-file evals/inspect.env inspect view` opens the logs: each question's conversation, tool calls and scores. The logs hold Statsguru's figures, so `evals/logs/` is gitignored and logs are never committed (D25).

Each question gets these scores, averaged over its epochs:

| Score | Passes when |
|---|---|
| `all_checks` | Every check below passes. A model's headline score is its mean. |
| `known_answer` | The final answer contains every fact in the case's known answer (R10), outside its links. |
| `answer_tool` | The answer tool the question needs was called and answered with a Statsguru query link, rather than only `query_stats`. A clarification doesn't count. |
| `arguments` | Each call the question needs was made, with the format, player, discipline, metrics, filters, period, minimum and split that decide its answer. A player named rather than given by ID counts once crickey resolves the name to them. |
| `proof` | The answer cites a Statsguru query link, not just a profile link, and every link it cites came from a tool result (D16). |
| `grounding` | Every figure in the answer appears in a tool result or in the question (D13). |
| `cost` | Not a check: tool calls, tool errors, Statsguru requests and cached pages (from each result's `_meta`, D38), tokens and seconds. |

## Adding or changing a golden question
Edit `golden_questions.toml`; nothing else lists the golden questions. Check the case's known answer live with crickey's tools, within D7–D12, and record it in R10.
