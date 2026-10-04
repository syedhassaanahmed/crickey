from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path

from crickey.fetcher import Fetcher, Freshness
from crickey.settings import Settings

CLASS_IDS = (1, 2, 3, 11, 6)
STAT_TYPES = ("batting", "bowling", "fielding", "allround", "fow", "team", "aggregate")
CATALOG_PLAYER_ID = 348144


def advanced_form_url(class_id: int, stat_type: str) -> str:
    return (
        "https://stats.cricinfo.com/ci/engine/stats/index.html?"
        f"class={class_id};filter=advanced;type={stat_type}"
    )


def player_form_url(class_id: int, player_id: int = CATALOG_PLAYER_ID) -> str:
    return (
        f"https://stats.cricinfo.com/ci/engine/player/{player_id}.html?"
        f"class={class_id};type=batting"
    )


def advanced_page_name(class_id: int, stat_type: str) -> str:
    return f"query_catalog_class_{class_id}_{stat_type}_advanced.html"


def player_page_name(class_id: int) -> str:
    return f"query_catalog_class_{class_id}_player_batting.html"


async def fetch_lookup_pages(
    requests: Iterable[tuple[str, str, str]],
    *,
    save_pages: Path | None,
    budget: float | timedelta,
) -> dict[str, str]:
    fetcher = Fetcher(Settings())
    pages: dict[str, str] = {}
    try:
        async with fetcher.call(budget=budget) as call:
            for key, page_name, url in requests:
                html = await call.fetch(url, freshness=Freshness.LOOKUP)
                pages[key] = html
                if save_pages is not None:
                    save_pages.mkdir(parents=True, exist_ok=True)
                    (save_pages / page_name).write_text(html, encoding="utf-8")
                    append_index(save_pages / "INDEX.md", page_name, url, status=200)
    finally:
        await fetcher.aclose()
    return pages


def read_lookup_pages(
    requests: Iterable[tuple[str, str, str]], *, input_pages: Path
) -> dict[str, str]:
    return {
        key: (input_pages / page_name).read_text(encoding="utf-8")
        for key, page_name, _url in requests
    }


def append_index(index_path: Path, page_name: str, url: str, *, status: int) -> None:
    line = f"| {page_name} | {status} | Query catalog generator lookup page | {url} |\n"
    if index_path.exists() and page_name in index_path.read_text(encoding="utf-8"):
        return
    with index_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def ids_requests() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            str(class_id),
            f"gen_ids_class_{class_id}_advanced_batting.html",
            advanced_form_url(class_id, "batting"),
        )
        for class_id in CLASS_IDS
    )


def catalog_requests() -> tuple[tuple[str, str, str], ...]:
    advanced = tuple(
        (
            f"{class_id}:{stat_type}",
            advanced_page_name(class_id, stat_type),
            advanced_form_url(class_id, stat_type),
        )
        for class_id in CLASS_IDS
        for stat_type in STAT_TYPES
    )
    players = tuple(
        (
            f"{class_id}:player",
            player_page_name(class_id),
            player_form_url(class_id),
        )
        for class_id in CLASS_IDS
    )
    return advanced + players
