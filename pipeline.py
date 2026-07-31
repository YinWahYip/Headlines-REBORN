import json
import time
import anthropic
from config import ANTHROPIC_API_KEY, CATEGORIES
import db

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

BATCH_SIZE = 40
BATCH_DELAY_SEC = 2

SYSTEM_PROMPT = f"""You are a news editor. Given a numbered list of headlines from multiple outlets, you must:
1. Group headlines that cover the same underlying story into clusters
2. Assign each cluster exactly one category from this list: {', '.join(CATEGORIES)}
3. Write a 1-2 sentence neutral summary of each story

Rules:
- Every headline index must appear in exactly one cluster
- A headline with no match can be its own single-item cluster
- Return ONLY valid JSON — no markdown, no explanation

Output format — a JSON array where each element is:
{{
  "indices": [list of headline indices that belong to this story],
  "category": "one of the allowed categories",
  "summary": "1-2 sentence neutral summary"
}}"""


def _parse_llm_response(text: str) -> list:
    """Strip markdown fences if present, then parse JSON."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        # parts[1] is the content between first pair of fences
        text = parts[1].lstrip("json").strip()
    return json.loads(text)


def _process_batch(batch: list[dict]) -> list[int]:
    """Send one batch of articles to the LLM. Returns cluster IDs created."""
    headlines_block = "\n".join(
        f"{i}. [{a['source']}] {a['title']}"
        for i, a in enumerate(batch)
    )
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Group these headlines into stories:\n\n{headlines_block}"}],
        )
        clusters_data = _parse_llm_response(response.content[0].text)
    except json.JSONDecodeError as e:
        print(f"[pipeline] JSON parse error: {e}")
        print(f"[pipeline] Raw response: {response.content[0].text[:500]}")
        return []
    except Exception as e:
        print(f"[pipeline] LLM error: {e}")
        return []

    cluster_ids = []
    for c in clusters_data:
        indices = c.get("indices", [])
        matched = [batch[i] for i in indices if 0 <= i < len(batch)]
        if not matched:
            continue
        outlets = sorted({a["source"] for a in matched})
        article_ids = [a["id"] for a in matched]
        cid = db.insert_cluster(
            summary=c["summary"],
            category=c["category"],
            outlets=outlets,
            article_ids=article_ids,
        )
        cluster_ids.append(cid)
    return cluster_ids


def process_articles(articles: list[dict]) -> list[int]:
    """
    Split articles into batches, run each through the LLM pipeline.
    Pauses between batches to respect rate limits.
    Returns all cluster IDs created.
    """
    if not articles:
        return []

    all_cluster_ids = []
    batches = [articles[i:i + BATCH_SIZE] for i in range(0, len(articles), BATCH_SIZE)]

    for n, batch in enumerate(batches):
        print(f"[pipeline] Batch {n + 1}/{len(batches)} ({len(batch)} articles)...")
        ids = _process_batch(batch)
        all_cluster_ids.extend(ids)
        if n < len(batches) - 1:
            time.sleep(BATCH_DELAY_SEC)

    print(f"[pipeline] {len(articles)} articles → {len(all_cluster_ids)} clusters total")
    return all_cluster_ids
