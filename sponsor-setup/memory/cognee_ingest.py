"""Ingest synthetic text and recall it; requires configured LLM/embeddings."""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parents[1] / ".env", override=False)
os.environ.setdefault("TELEMETRY_DISABLED", "true")
os.environ.setdefault("DATA_ROOT_DIRECTORY", str(HERE / ".data_storage"))
os.environ.setdefault("SYSTEM_ROOT_DIRECTORY", str(HERE / ".cognee_system"))
os.environ.setdefault("COGNEE_LOGS_DIR", str(HERE / "logs"))


async def main():
    if not os.getenv("LLM_API_KEY"):
        raise SystemExit("Set LLM_API_KEY in the workspace .env, then rerun. The keyless check is bash sponsor-setup/memory/cognee_demo.sh.")
    import cognee

    await cognee.remember(
        (HERE / "sample.txt").read_text(),
        dataset_name="hackathon_setup",
        self_improvement=False,
    )
    answers = await cognee.recall(
        query_text="Who owns ticket DEMO-101, and what resolved it?",
        datasets=["hackathon_setup"],
    )
    if not answers:
        raise RuntimeError("Cognee returned no recall results.")
    for answer in answers:
        print(answer.text)


if __name__ == "__main__":
    asyncio.run(main())
