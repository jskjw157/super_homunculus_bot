"""Generic Playwright-based browser automation wrapper.

Provides a high-level, command-driven interface over Playwright Chromium.
All state (cookies, screenshots) is scoped per ``site_key`` and persisted
under a ``browser_sessions/`` directory relative to the project root.

No site-specific logic, credentials, or hardcoded URLs are present here.
Callers supply everything at runtime via ``execute()``.

Example::

    async with BrowserAutomation(base_dir="/project") as browser:
        result = await browser.execute("mysite", "navigate", url="https://example.com")
        if result["ok"]:
            print(result["result"])
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

# site_key must be alphanumeric, underscore, or hyphen, 1–64 characters.
_SITE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Allowlist for CSS selectors: letters, digits, and common selector characters.
# Deliberately restrictive to prevent injection via crafted selectors.
_CSS_SELECTOR_SAFE_RE = re.compile(r'^[A-Za-z0-9_\-\.\#\[\]="\':,\s\>\+\~\*\^\$\|]{1,512}$')

# Commands exposed through execute().
_SUPPORTED_COMMANDS: frozenset[str] = frozenset({
    "navigate",
    "get_page_content",
    "scroll_and_collect",
    "screenshot",
    "click",
    "type_text",
    "evaluate",
    "upload_file",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(result: Any = None) -> dict[str, Any]:
    """Build a successful response envelope."""
    return {"ok": True, "result": result, "error": ""}


def _err(message: str) -> dict[str, Any]:
    """Build a failure response envelope."""
    return {"ok": False, "result": None, "error": message}


def _validate_site_key(site_key: str) -> None:
    """Raise ValueError when *site_key* contains disallowed characters."""
    if not _SITE_KEY_RE.match(site_key):
        raise ValueError(
            f"Invalid site_key {site_key!r}. "
            "Only alphanumeric characters, underscores, and hyphens are allowed (1–64 chars)."
        )


def _validate_selector(selector: str) -> None:
    """Raise ValueError when *selector* fails the CSS allowlist check."""
    if not _CSS_SELECTOR_SAFE_RE.match(selector):
        raise ValueError(
            f"Selector {selector!r} failed safety validation. "
            "Use plain CSS selectors without special shell or script characters."
        )


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class BrowserAutomation:
    """Async context manager wrapping a persistent Playwright Chromium browser.

    Args:
        base_dir: Absolute path to the project root.  All relative paths
            (sessions directory, screenshots) are resolved from here.
        headless: When *True* (default) the browser runs without a visible
            window.  Set to *False* for local debugging.
        timeout: Default navigation/network-idle timeout in milliseconds.

    Usage::

        async with BrowserAutomation(base_dir="/project") as ba:
            result = await ba.execute("wiki", "get_page_content", url="https://en.wikipedia.org/wiki/Python_(programming_language)")
            html = result["result"]["html"]
    """

    #: Sub-directory (under *base_dir*) used for session cookie files and
    #: screenshot output.
    SESSIONS_DIR: str = "browser_sessions"

    def __init__(
        self,
        base_dir: str | None = None,
        *,
        headless: bool = True,
        timeout: int = 30_000,
    ) -> None:
        self._base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parents[3]
        self._sessions_dir = self._base_dir / self.SESSIONS_DIR
        self._headless = headless
        self._timeout = timeout

        # Populated in __aenter__
        self._playwright: Any = None
        self._browser: Any = None
        # site_key -> BrowserContext
        self._contexts: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "BrowserAutomation":
        """Launch Playwright and Chromium."""
        try:
            from playwright.async_api import async_playwright  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed. "
                "Install it with: pip install playwright && playwright install chromium"
            ) from exc

        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        logger.info("Chromium launched (headless=%s).", self._headless)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Persist cookies and shut down Playwright."""
        for site_key, ctx in self._contexts.items():
            await self._save_cookies(site_key, ctx)

        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

        logger.info("Chromium shut down. %d site context(s) closed.", len(self._contexts))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        site_key: str,
        command: str,
        **params: Any,
    ) -> dict[str, Any]:
        """Dispatch a browser command for the given site.

        Args:
            site_key: Logical identifier for the website session
                (alphanumeric, underscores, and hyphens only).
            command: One of the supported command names.
            **params: Command-specific keyword arguments (see each
                ``_cmd_*`` method for accepted parameters).

        Returns:
            A dict with keys ``ok`` (bool), ``result`` (any), and
            ``error`` (str).  On success ``error`` is an empty string;
            on failure ``result`` is *None*.
        """
        try:
            _validate_site_key(site_key)
        except ValueError as exc:
            return _err(str(exc))

        if command not in _SUPPORTED_COMMANDS:
            return _err(
                f"Unknown command {command!r}. "
                f"Supported: {sorted(_SUPPORTED_COMMANDS)}"
            )

        if self._browser is None:
            return _err("BrowserAutomation is not running. Use it as an async context manager.")

        ctx = await self._get_context(site_key)

        try:
            handler = getattr(self, f"_cmd_{command}")
            return await handler(ctx, **params)
        except Exception as exc:
            logger.exception("Command %r failed for site_key=%r.", command, site_key)
            return _err(f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # Session / cookie helpers
    # ------------------------------------------------------------------

    def _cookie_path(self, site_key: str) -> Path:
        """Return the path to the JSON cookie file for *site_key*."""
        return self._sessions_dir / f"{site_key}_cookies.json"

    async def _get_context(self, site_key: str) -> Any:
        """Return (or create) the Playwright BrowserContext for *site_key*.

        On first access the context is created and existing cookies are
        loaded from disk so the session is resumed automatically.
        """
        if site_key in self._contexts:
            return self._contexts[site_key]

        ctx = await self._browser.new_context()
        cookie_path = self._cookie_path(site_key)

        if cookie_path.exists():
            try:
                cookies: list[dict[str, Any]] = json.loads(cookie_path.read_text(encoding="utf-8"))
                await ctx.add_cookies(cookies)
                logger.info("Loaded %d cookie(s) for site_key=%r.", len(cookies), site_key)
            except Exception as exc:
                logger.warning("Cookie load failed for site_key=%r: %s", site_key, exc)

        self._contexts[site_key] = ctx
        return ctx

    async def _save_cookies(self, site_key: str, ctx: Any) -> None:
        """Persist all cookies for *ctx* to disk as JSON."""
        try:
            cookies = await ctx.cookies()
            self._cookie_path(site_key).write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug("Saved %d cookie(s) for site_key=%r.", len(cookies), site_key)
        except Exception as exc:
            logger.warning("Cookie save failed for site_key=%r: %s", site_key, exc)

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------

    async def _cmd_navigate(self, ctx: Any, *, url: str, **_: Any) -> dict[str, Any]:
        """Navigate to *url* and wait for network to become idle.

        Args:
            url: Fully-qualified URL to navigate to.

        Returns:
            Result contains the final URL after any redirects.
        """
        page = await ctx.new_page()
        try:
            response = await page.goto(url, wait_until="networkidle", timeout=self._timeout)
            final_url: str = page.url
            status: int | None = response.status if response else None
            return _ok({"url": final_url, "status": status})
        finally:
            await page.close()

    async def _cmd_get_page_content(self, ctx: Any, *, url: str, **_: Any) -> dict[str, Any]:
        """Fetch a page and return its visible text and raw HTML.

        Args:
            url: URL to retrieve.

        Returns:
            Result is ``{"text": str, "html": str, "url": str}``.
        """
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=self._timeout)
            text: str = await page.inner_text("body")
            html: str = await page.content()
            return _ok({"text": text, "html": html, "url": page.url})
        finally:
            await page.close()

    async def _cmd_scroll_and_collect(
        self,
        ctx: Any,
        *,
        url: str,
        max_scrolls: int = 10,
        link_selector: str = "a[href]",
        **_: Any,
    ) -> dict[str, Any]:
        """Navigate to *url*, scroll incrementally, and collect unique links.

        Args:
            url: URL to scrape.
            max_scrolls: Maximum number of scroll steps before stopping.
            link_selector: CSS selector used to locate link elements.
                Defaults to ``a[href]``.

        Returns:
            Result is ``{"links": list[str], "scroll_steps": int}``.
        """
        try:
            _validate_selector(link_selector)
        except ValueError as exc:
            return _err(str(exc))

        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=self._timeout)

            links: set[str] = set()
            steps_done = 0

            for _ in range(max(1, max_scrolls)):
                # Collect links at current scroll position.
                elements = await page.query_selector_all(link_selector)
                for el in elements:
                    href: str | None = await el.get_attribute("href")
                    if href and href.startswith("http"):
                        links.add(href)

                # Scroll one viewport down.
                previous_height: float = await page.evaluate("document.documentElement.scrollHeight")
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(600)
                new_height: float = await page.evaluate("document.documentElement.scrollHeight")
                steps_done += 1

                if new_height == previous_height:
                    # Reached the bottom; no more content to load.
                    break

            return _ok({"links": sorted(links), "scroll_steps": steps_done})
        finally:
            await page.close()

    async def _cmd_screenshot(self, ctx: Any, **_: Any) -> dict[str, Any]:
        """Capture a screenshot of the current page.

        The image is saved to ``browser_sessions/`` with a timestamp-based
        filename.

        Returns:
            Result is the absolute path string of the saved screenshot file.
        """
        pages = ctx.pages
        if not pages:
            return _err("No open page in this context. Navigate first.")

        page = pages[-1]
        filename = f"screenshot_{int(time.time() * 1000)}.png"
        output_path = self._sessions_dir / filename
        await page.screenshot(path=str(output_path), full_page=True)
        logger.info("Screenshot saved: %s", output_path)
        return _ok(str(output_path))

    async def _cmd_click(self, ctx: Any, *, selector: str, **_: Any) -> dict[str, Any]:
        """Click an element identified by *selector*.

        Args:
            selector: CSS selector for the target element.

        Returns:
            Result is *True* on success.
        """
        try:
            _validate_selector(selector)
        except ValueError as exc:
            return _err(str(exc))

        pages = ctx.pages
        if not pages:
            return _err("No open page in this context. Navigate first.")

        page = pages[-1]
        await page.click(selector, timeout=self._timeout)
        await page.wait_for_load_state("networkidle", timeout=self._timeout)
        return _ok(True)

    async def _cmd_type_text(
        self,
        ctx: Any,
        *,
        selector: str,
        text: str,
        **_: Any,
    ) -> dict[str, Any]:
        """Type *text* into the input field identified by *selector*.

        The field is cleared before typing begins.

        Args:
            selector: CSS selector for the ``<input>`` or ``<textarea>``.
            text: The string to type.

        Returns:
            Result is *True* on success.
        """
        try:
            _validate_selector(selector)
        except ValueError as exc:
            return _err(str(exc))

        pages = ctx.pages
        if not pages:
            return _err("No open page in this context. Navigate first.")

        page = pages[-1]
        await page.fill(selector, text, timeout=self._timeout)
        return _ok(True)

    async def _cmd_evaluate(self, ctx: Any, *, js: str, **_: Any) -> dict[str, Any]:
        """Execute arbitrary JavaScript in the page context.

        Args:
            js: JavaScript expression or function body.  The return value
                must be JSON-serialisable.

        Returns:
            Result is whatever the JavaScript expression evaluates to.
        """
        pages = ctx.pages
        if not pages:
            return _err("No open page in this context. Navigate first.")

        page = pages[-1]
        value: Any = await page.evaluate(js)
        return _ok(value)

    async def _cmd_upload_file(
        self,
        ctx: Any,
        *,
        selector: str,
        file_path: str,
        url: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Upload a local file to a ``<input type="file">`` element.

        Args:
            selector: CSS selector for the file input element.
            file_path: Absolute or project-relative path to the file to upload.
            url: Optional URL to navigate to before uploading.  If omitted
                the current page is used.

        Returns:
            Result is *True* on success.
        """
        try:
            _validate_selector(selector)
        except ValueError as exc:
            return _err(str(exc))

        # Resolve file path relative to base_dir if not absolute.
        resolved = Path(file_path)
        if not resolved.is_absolute():
            resolved = self._base_dir / resolved

        if not resolved.exists():
            return _err(f"File not found: {resolved}")

        page = await ctx.new_page() if url else (ctx.pages[-1] if ctx.pages else None)
        if page is None:
            return _err("No open page in this context and no url provided.")

        try:
            if url:
                await page.goto(url, wait_until="networkidle", timeout=self._timeout)

            await page.set_input_files(selector, str(resolved), timeout=self._timeout)
            return _ok(True)
        finally:
            if url:
                await page.close()
