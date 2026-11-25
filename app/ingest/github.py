""" paring repo urls,resolve ref to commit SHA,downloading the tarball """

from __future__ import annotations
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit
import httpx

from app.errors import(
    GitHubRateLimited,GitHubUnavailable, InvalidRepoURL,ToolError,RefNotFound,RepoTooLarge,RepoNotFound
)

_GITHUB_HOSTS={"github.com","www.github.com"}
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REF_MARKERS = {"tree", "blob", "commit"}

@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str
    ref: str | None = None

    @property
    def slug(self)->str:
        return f"{self.owner}/{self.repo}"

@dataclass(frozen=True)
class ResolvedRepo:
    owner: str  
    repo: str
    ref: str  
    sha: str

    @property
    def slug(self)->str:
        return f"{self.owner}/{self.repo}"

def parse_github_url(url: str, ref: str| None=None)-> RepoRef:
    s= (url or "").strip()
    if not s:
        raise InvalidRepoURL("repo url is empty")
    if s.startswith("git@"):
        host,_, path= s[4:].partition(":")
        if host.lower() not in _GITHUB_HOSTS or not path:
            raise InvalidRepoURL(f"not a github url: {url!r}")
    else:
        if "://" not in s:
            s = "https://" + s
        parts = urlsplit(s)
        if parts.scheme not in ("http", "https"):
            raise InvalidRepoURL(f"unsupported URL scheme in {url!r}")
        if (parts.hostname or "").lower() not in _GITHUB_HOSTS:
            raise InvalidRepoURL(f"only github.com URLs are supported, got {url!r}")
        path = parts.path

    segs= [p for p in path.split("/") if p]
    if len(segs) < 2:
        raise InvalidRepoURL(f"expected github.com/<owner>/<repo>, got {url!r}")

    owner,repo =segs[0],segs[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _OWNER_RE.match(owner) or not _REPO_RE.match(repo) or repo in {".", ".."}:
        raise InvalidRepoURL(f"invalid owner/repo in {url!r}")

    url_ref =segs[3] if len(segs)>= 4 and segs[2] in _REF_MARKERS else None
    return RepoRef(owner=owner, repo=repo, ref=ref or url_ref)

class GithubClient:
    def __init__(self,
        token: str | None = None,
        *,
        api_base: str = "https://api.github.com",
        timeout_s: float = 30.0,
        resolve_cache_ttl_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,):
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "codebase-agent",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        self._http = httpx.AsyncClient(
            base_url=api_base,
            headers=headers,
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self._ttl = resolve_cache_ttl_s
        self._resolve_cache: dict[tuple[str, str, str | None], tuple[float, ResolvedRepo]] = {}

    async def aclose(self)-> None:
        await self._http.aclose()

    async def __aenter__(self) -> GithubClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # resolve
    async def resolve(self, ref: RepoRef)-> ResolvedRepo:
        key= (ref.owner.lower(), ref.repo.lower(),ref.ref)
        hit = self._resolve_cache.get(key)
        if hit and time.monotonic()- hit[0] < self._ttl:
            return hit[1]

        meta = await self._get(f"/repos/{ref.owner}/{ref.repo}", not_found=RepoNotFound(
            f"repository {ref.slug} not found (or it is private; only public repos are supported)"
        ))
        data = meta.json()
        if data.get("private"):
            raise RepoNotFound(f"{ref.slug} is private; only public repos are supported")

        owner, repo = data["owner"]["login"], data["name"]
        want = ref.ref or data["default_branch"]
        resp = await self._get(
            f"/repos/{owner}/{repo}/commits/{quote(want, safe='/')}",
            headers={"Accept": "application/vnd.github.sha"},
            not_found=RefNotFound(f"ref {want!r} not found in {owner}/{repo}"),
        )
        sha = resp.text.strip()
        if not _SHA_RE.match(sha):
            raise GitHubUnavailable(f"unexpected SHA response from GitHub: {sha[:80]!r}")

        resolved = ResolvedRepo(owner=owner, repo=repo, ref=want, sha=sha)
        self._resolve_cache[key] = (time.monotonic(), resolved)
        return resolved

    # download
    async def download_tarball(self, repo: ResolvedRepo, dest: Path, *, max_bytes: int) -> int:
        """Stream the .tar.gz for repo@sha to `dest`. Returns bytes written."""
        too_big = RepoTooLarge(
            f"{repo.slug} archive exceeds {max_bytes // (1024 * 1024)} MB; refusing to download"
        )
        try:
            async with self._http.stream("GET", f"/repos/{repo.owner}/{repo.repo}/tarball/{repo.sha}") as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    self._raise_for(resp, RepoNotFound(f"archive for {repo.slug}@{repo.sha[:7]} not found"))
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise too_big
                written = 0
                with dest.open("wb") as f:
                    async for chunk in resp.aiter_bytes():
                        written += len(chunk)
                        if written > max_bytes:
                            raise too_big
                        f.write(chunk)
                return written
        except httpx.HTTPError as e:
            raise GitHubUnavailable(f"downloading {repo.slug} failed: {e}") from e

    # helpers
    async def _get(self,path:str, *, not_found: Exception,headers: dict|None =None) ->httpx.Response:
        try:
            resp =await self._http.get(path, headers=headers)
        except httpx.HTTPError as e:
            raise GitHubUnavailable(f"Github request failed:{e}") from e
        if resp.status_code != 200:
            self._raise_for(resp, not_found)
        return resp

    @staticmethod
    def _raise_for(resp: httpx.Response, not_found: Exception) -> None:
        status = resp.status_code
        # 422: "No commit found for SHA"; 409: empty repository
        if status in (404, 422):
            raise not_found
        if status == 409:
            raise RepoNotFound("repository is empty")
        if status in (403, 429) and (
            resp.headers.get("x-ratelimit-remaining") == "0" or "rate limit" in resp.text.lower()
        ):
            raise GitHubRateLimited(
                "GitHub API rate limit hit; set GITHUB_TOKEN to raise the limit (60/h -> 5000/h)"
            )
        raise GitHubUnavailable(f"GitHub returned HTTP {status}: {resp.text[:200]}")

