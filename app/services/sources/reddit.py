from datetime import datetime, timezone
import re

import httpx

from app.config import settings
from app.services.sources.base import SourceAdapter, Story


class RedditAdapter(SourceAdapter):
    name = "reddit"
    _base_url = "https://www.reddit.com"
    _nsfw_pattern = re.compile(r"\b(nsfw|18\+|porn|xxx|onlyfans|nude)\b", flags=re.IGNORECASE)

    async def fetch_stories(self) -> list[Story]:
        subreddits = [x.strip() for x in settings.reddit_subreddits.split(",") if x.strip()]
        if not subreddits:
            return []

        headers = {"User-Agent": settings.reddit_user_agent}
        stories: list[Story] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as client:
            for subreddit in subreddits:
                payload = await self._fetch_subreddit_feed(client, subreddit)
                children = ((payload or {}).get("data") or {}).get("children") or []
                for child in children:
                    data = child.get("data") or {}
                    if not data:
                        continue

                    if data.get("stickied"):
                        continue
                    if self._is_nsfw(data):
                        continue

                    external_id = str(data.get("name") or data.get("id") or "").strip()
                    if not external_id or external_id in seen:
                        continue
                    seen.add(external_id)

                    permalink = str(data.get("permalink") or "").strip()
                    post_url = str(data.get("url") or "").strip()
                    if permalink and permalink.startswith("/"):
                        permalink = f"https://www.reddit.com{permalink}"
                    url = post_url or permalink or f"https://www.reddit.com/r/{subreddit}/"

                    timestamp = data.get("created_utc")
                    published_at = (
                        datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
                        if isinstance(timestamp, (int, float))
                        else None
                    )

                    stories.append(
                        Story(
                            source=self.name,
                            feed=subreddit,
                            external_id=external_id,
                            title=(data.get("title") or "Untitled").strip(),
                            url=url,
                            score=int(data.get("score") or 0),
                            comment_count=int(data.get("num_comments") or 0),
                            published_at=published_at,
                            author=(data.get("author") or "").strip() or None,
                            text=(data.get("selftext") or "").strip() or None,
                            top_comments=[],
                            raw=data,
                        )
                    )

                    if len(stories) >= settings.reddit_max_total:
                        return stories

        return stories

    async def _fetch_subreddit_feed(self, client: httpx.AsyncClient, subreddit: str) -> dict:
        response = await client.get(
            f"{self._base_url}/r/{subreddit}/hot.json",
            params={
                "limit": settings.reddit_max_per_subreddit,
                "raw_json": 1,
            },
        )
        response.raise_for_status()
        return response.json() or {}

    def _is_nsfw(self, item: dict) -> bool:
        if bool(item.get("over_18")) or bool(item.get("subreddit_over18")):
            return True

        joined = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("selftext") or ""),
                str(item.get("link_flair_text") or ""),
            ]
        )
        return bool(self._nsfw_pattern.search(joined))
