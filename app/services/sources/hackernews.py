from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.sources.base import SourceAdapter, Story


class HackerNewsAdapter(SourceAdapter):
    name = "hackernews"
    _base_url = "https://hacker-news.firebaseio.com/v0"
    _feeds = ("topstories", "newstories", "showstories", "askstories")

    async def fetch_stories(self) -> list[Story]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            ids: list[tuple[str, int]] = []
            for feed in self._feeds:
                feed_ids = await self._fetch_feed_ids(client, feed)
                ids.extend((feed, story_id) for story_id in feed_ids[: settings.hn_max_per_feed])

            deduped_ids: list[tuple[str, int]] = []
            seen: set[int] = set()
            for feed, story_id in ids:
                if story_id in seen:
                    continue
                deduped_ids.append((feed, story_id))
                seen.add(story_id)
                if len(deduped_ids) >= settings.hn_max_total:
                    break

            stories: list[Story] = []
            for feed, story_id in deduped_ids:
                item = await self._fetch_item(client, story_id)
                if not item or item.get("type") != "story":
                    continue

                top_comments: list[str] = []
                if settings.hn_include_top_comments:
                    comment_ids = (item.get("kids") or [])[: settings.hn_top_comments_per_story]
                    for comment_id in comment_ids:
                        comment = await self._fetch_item(client, comment_id)
                        if not comment:
                            continue
                        text = (comment.get("text") or "").strip()
                        if text:
                            top_comments.append(text)

                timestamp = item.get("time")
                published_at = (
                    datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
                    if isinstance(timestamp, (int, float))
                    else None
                )

                stories.append(
                    Story(
                        source=self.name,
                        feed=feed,
                        external_id=str(item.get("id", story_id)),
                        title=(item.get("title") or "Untitled").strip(),
                        url=(item.get("url") or f"https://news.ycombinator.com/item?id={story_id}").strip(),
                        score=int(item.get("score") or 0),
                        comment_count=int(item.get("descendants") or 0),
                        published_at=published_at,
                        author=item.get("by"),
                        text=item.get("text"),
                        top_comments=top_comments,
                        raw=item,
                    )
                )

            return stories

    async def _fetch_feed_ids(self, client: httpx.AsyncClient, feed: str) -> list[int]:
        response = await client.get(f"{self._base_url}/{feed}.json")
        response.raise_for_status()
        data = response.json() or []
        return [int(story_id) for story_id in data if isinstance(story_id, int)]

    async def _fetch_item(self, client: httpx.AsyncClient, item_id: int) -> dict | None:
        response = await client.get(f"{self._base_url}/item/{item_id}.json")
        response.raise_for_status()
        return response.json()
