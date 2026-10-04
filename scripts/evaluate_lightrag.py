"""Real, bounded local Ollama/LightRAG probe. Run with evaluation extras installed."""

import asyncio
import json
import tempfile
import time
from pathlib import Path

import numpy as np
import ollama
from lightrag import LightRAG, QueryParam
from lightrag.kg.shared_storage import initialize_pipeline_status
from lightrag.utils import EmbeddingFunc

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "docs/reports/model-benchmark.json"


async def main():
    client = ollama.AsyncClient(host="http://127.0.0.1:11434", timeout=100)
    report = {
        "chat_model": "qwen3.5:9b",
        "embedding_model": "all-minilm:l6-v2",
        "lightrag_version": "1.5.7",
        "calls": [],
        "operations": {},
    }

    def save():
        RESULT.write_text(json.dumps(report, indent=2))

    async def embed(texts, **kwargs):
        start = time.perf_counter()
        result = await client.embed(model=report["embedding_model"], input=texts)
        report["calls"].append(
            {
                "kind": "embedding",
                "seconds": time.perf_counter() - start,
                "count": len(texts),
                "dimensions": len(result.embeddings[0]),
                "prompt_eval_count": result.prompt_eval_count,
            }
        )
        save()
        return np.array(result.embeddings)

    async def complete(prompt, system_prompt=None, history_messages=None, **kwargs):
        messages = (
            ([{"role": "system", "content": system_prompt}] if system_prompt else [])
            + (history_messages or [])
            + [{"role": "user", "content": prompt}]
        )
        start = time.perf_counter()
        result = await client.chat(
            model=report["chat_model"],
            messages=messages,
            think=False,
            options={"temperature": 0, "num_ctx": 8192, "num_predict": 2048},
        )
        report["calls"].append(
            {
                "kind": "chat",
                "seconds": time.perf_counter() - start,
                "prompt_eval_count": result.prompt_eval_count,
                "eval_count": result.eval_count,
                "output": result.message.content,
            }
        )
        save()
        return result.message.content

    await embed(["Dev Shah owns Beacon. Mira Chen leads Platform."])
    report["operations"]["structured_extraction"] = await complete(
        "Extract source, target, relation, quote as a JSON array only. "
        "Quote must be verbatim. Text: Dev Shah owns Beacon. Mira Chen leads Platform."
    )
    with tempfile.TemporaryDirectory(prefix="company-lightrag-") as directory:
        rag = LightRAG(
            working_dir=directory,
            llm_model_func=complete,
            llm_model_name=report["chat_model"],
            embedding_func=EmbeddingFunc(
                embedding_dim=384,
                func=embed,
                max_token_size=256,
                model_name=report["embedding_model"],
            ),
            entity_extract_max_gleaning=0,
            llm_model_max_async=1,
            max_parallel_insert=1,
            chunk_token_size=400,
            chunk_overlap_token_size=40,
            default_llm_timeout=100,
        )
        await rag.initialize_storages()
        await initialize_pipeline_status()
        try:
            docs = [
                "Northstar Signal builds industrial telemetry. Dev Shah owns Beacon. "
                "Beacon monitors cold-chain sensor streams for customer Alder Foods.",
                "Alder Foods runs the Beacon pilot at its Pune depot. "
                "Beacon uses HarborLink gateways supplied by vendor Kestrel Systems. "
                "Dev Shah approves pilot releases.",
            ]
            start = time.perf_counter()
            await rag.ainsert(
                docs, ids=["probe-company", "probe-pilot"], file_paths=["company.md", "pilot.md"]
            )
            report["operations"]["insert_seconds"] = time.perf_counter() - start
            graph = await rag.get_knowledge_graph(node_label="*", max_nodes=100)
            report["operations"]["graph"] = graph.model_dump()
            question = (
                "Who owns Beacon, which customer pilots it, and which vendor supplies its gateways?"
            )
            start = time.perf_counter()
            answer = await rag.aquery(
                question,
                param=QueryParam(
                    mode="hybrid",
                    include_references=True,
                    enable_rerank=False,
                    top_k=8,
                    chunk_top_k=4,
                ),
            )
            report["operations"]["query"] = {
                "seconds": time.perf_counter() - start,
                "question": question,
                "answer": answer,
            }
            start = time.perf_counter()
            followup = await rag.aquery(
                "Where does that customer run its pilot?",
                param=QueryParam(
                    mode="hybrid",
                    include_references=True,
                    enable_rerank=False,
                    top_k=8,
                    chunk_top_k=4,
                    conversation_history=[
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": answer},
                    ],
                ),
            )
            report["operations"]["followup"] = {
                "seconds": time.perf_counter() - start,
                "answer": followup,
            }
            start = time.perf_counter()
            deleted = await rag.adelete_by_doc_id("probe-pilot")
            report["operations"]["delete"] = {
                "seconds": time.perf_counter() - start,
                "result": str(deleted),
                "graph_after": (
                    await rag.get_knowledge_graph(node_label="*", max_nodes=100)
                ).model_dump(),
            }
        except Exception as error:
            report["error"] = f"{type(error).__name__}: {error}"
        finally:
            await rag.finalize_storages()
            save()


if __name__ == "__main__":
    asyncio.run(main())
