# Evals

Language models answer crickey's golden questions (plan › Goal) through crickey, and deterministic checks score each answer (#18). Use the scores to see which models can use crickey, to catch regressions, and to find where tool descriptions trip small models up (D13, D14).

The questions ask live Statsguru through a running crickey, so a run follows D7–D12: one request every 15 seconds, and a block stops the run. Every question has an answer that can't change (D37). [docs/plan.md › Evals](../docs/plan.md#evals) has the design, and [docs/research.md › 17](../docs/research.md#17-evals-with-inspect-ai-and-ollama) the facts behind it.

## Run it on Windows
1. **Start crickey** in another terminal, from this checkout or the Docker image (README):
   ```powershell
   uv run crickey serve
   ```
2. **Set up Ollama:**
   - Set the context length to at least 32k: the slider in the Ollama app's settings, or `OLLAMA_CONTEXT_LENGTH`. A model reads about 8,000 tokens of tool definitions and instructions before each question.
   - Leave the cache and flash attention at Ollama's defaults. An 8-bit cache reads prompts about 4.6 times slower on a CPU.
   - Set `LLAMA_ARG_CACHE_RAM=0` as a user environment variable and restart Ollama. Otherwise Ollama keeps up to 8 GiB of earlier conversations.
   - Pull a model that calls tools and doesn't think, such as `ollama pull qwen3:4b-instruct`. `qwen3:4b` always thinks first, which takes minutes on a CPU.
3. **Run the eval:**
   ```powershell
   uv run --env-file evals/inspect.env inspect eval evals/golden_questions.py --model ollama/qwen3:4b-instruct
   ```
   `evals/inspect.env` writes JSON logs to `evals/logs`, and gives each model request an hour, because Inspect's HTTP clients give up after 10 minutes and a CPU can take longer than that to read the first prompt.

`uv sync` installs the eval's tools (the `evals` dependency group). The run asks each of the nine questions three times (epochs), one question at a time.

## Options
- `--epochs 1` asks each question once. On a laptop CPU, the model reads for 5 to 10 minutes before it starts on the first question; later questions reuse that reading and take a few minutes each.
- `--sample-id q5-tendulkar-odi-world-cup-hundreds` asks one question; `evals/cases.toml` has the IDs.
- `-T crickey_url=http://127.0.0.1:8766/mcp` uses a crickey on another port.
- `--model` takes any of [Inspect's model providers](https://inspect.aisi.org.uk/providers.html), for example `openai/<model>` or `anthropic/<model>` with an API key in the environment. Several models run one after another and share crickey's cache, so only the first pays for the Statsguru requests (D17).

## Results
`uv run --env-file evals/inspect.env inspect view` opens the logs: each question's conversation, tool calls and scores. The logs hold Statsguru's figures, so `evals/logs/` is gitignored and logs are never committed (D25).

Each question gets these scores, averaged over its epochs:

| Score | Passes when |
|---|---|
| `all_checks` | Every check below passes. A model's headline score is its mean. |
| `known_answer` | The final answer contains every fact in the case's known answer (R10), outside its links. |
| `answer_tool` | The answer tool the question needs was called and answered with a Statsguru link, rather than only `query_stats`. A clarification doesn't count. |
| `arguments` | Each call the question needs was made, with the format, player, discipline, metrics, filters, period, minimum and split that decide its answer. A player named rather than given by ID counts once crickey resolves the name to them. |
| `proof` | The answer cites a Statsguru link, and every link it cites came from a tool result (D16). |
| `grounding` | Every figure in the answer appears in a tool result or in the question (D13). |
| `cost` | Not a check: tool calls, tool errors, Statsguru requests and cached pages (from each result's `_meta`, D38), tokens and seconds. |

## Cases
`evals/cases.toml` holds the cases: the question, the answer tool and the calls it needs, and the known answer's facts. A changed case needs a new live check of its known answer:
```powershell
uv run pytest -m live tests/test_evals_live.py
```
That makes about 25 requests, so it takes about 7 minutes. Record the answers in R10.
