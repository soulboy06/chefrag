# ChefRAG — Agentic RAG Cooking Assistant

A ReAct-style intelligent cooking assistant built on LangGraph. Beyond answering "what should I eat" or
"how do I cook this", it senses your city's weather and seasonal foods, reads your taste profile, recommends
recipes against what's actually in your fridge — and physically deducts ingredients from inventory once you
start cooking, closing the loop between LLM decisions and real-world state.

> The recipe corpus is Chinese; the engineering (retrieval, agent loop, degradation design) is the point.
> **Live demo:** https://chefrag.soulboy.xyz

## Highlights

- **LangGraph ReAct agent, 2-node topology** — an `agent_node` for decisions plus a `memory_node` for the
  taste profile. The chef autonomously calls 7 tools; ending a session archives the conversation into a
  persistent user profile (`user_profile.json`).
- **Hybrid retrieval with normalized weighted fusion** — ChromaDB dense semantic search + BM25 lexical
  search, fused with min-max normalized linear weighting, plus season boosting and title-match boosting
  to fix ranking inversions on fuzzy queries, typos, and small corpora.
- **Fridge linkage** — confirming a dish triggers `deduct_fridge_ingredients`, physically decrementing
  stock; automatically detects no-cook intents (office / no-stove / delivery) and relaxes the fridge filter.
- **4-level weather chain** — cascading weather providers with recursive retry and commercial/free domain
  failover for high-precision local sensing.
- **1-second graceful degradation** — if the LLM API is blocked/times out or the local vector lib breaks,
  the system silently falls back to local rule matching + BM25 + static seasonal tips. Never goes down.

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env
# set OPENAI_API_KEY (or a compatible base URL); without it, the app
# auto-detects and runs in local degraded mode — still fully usable
```

Web dashboard (recommended):

```bash
python web_server.py
# open http://127.0.0.1:8000
```

Terminal TUI, for the full cli experience:

```bash
python tui_app.py
# /exit archives the session into your taste profile
```

## Layout

```
src/
├─ agent/graph.py            # LangGraph core (agent_node + memory_node)
├─ rag/vector_store.py       # hybrid retrieval (ChromaDB + BM25, normalized fusion + boosting)
└─ tools/
   ├─ agent_tools.py         # 7 LangChain @tool plugins
   ├─ context_tool.py        # weather sensing + seasonal analysis
   ├─ inventory_tool.py      # fridge inventory CRUD + physical deduction
   └─ user_profile_tool.py   # taste-profile read/write
static/                      # dashboard frontend (vanilla HTML/CSS/JS)
tests/test_flow.py           # integration tests incl. no-cook + degradation scenarios
web_server.py                # FastAPI entry + static hosting
tui_app.py                   # terminal UI
```

## Evaluation

A 100-case test set plus `tests/test_flow.py` cover the core scenarios (fuzzy queries, no-cook intents,
degraded mode). The web UI exposes the rewritten query, filters, recalled chunks, and a RAG trace that
separates *no recall* / *wrong recall* / *generation error* — so bad cases are diagnosable, not mysterious.
