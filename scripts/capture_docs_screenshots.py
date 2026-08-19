"""Capture 16:9 documentation screenshots against the web-demo service.

Requires a running demo UI (default http://127.0.0.1:8001) populated from
docs/demo/asm_cleanup_demo.db, plus ASM_CLEANUP_PASSWORD for login.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VIEWPORT = {"width": 1920, "height": 1080}
DEFAULT_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_OUTPUT_DIR = Path("docs/images")
REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ if unset.

    Args:
        path (Path): Path to a dotenv-style file.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_login_password(cli_password: str | None) -> str:
    """Resolve the shared login password from CLI, env, or repo .env.

    Args:
        cli_password (str | None): Explicit --password value, if provided.

    Returns:
        str: Non-empty password, or empty string when unavailable.
    """
    if cli_password:
        return cli_password
    _load_dotenv(Path.cwd() / ".env")
    _load_dotenv(REPO_ROOT / ".env")
    return os.environ.get("ASM_CLEANUP_PASSWORD", "").strip()


def _wait_for_server(base_url: str, attempts: int = 60) -> None:
    """Poll the demo UI until it responds or raise after timeout.

    Args:
        base_url (str): Origin URL of the demo web service.
        attempts (int): Number of one-second polls before failing.

    Raises:
        RuntimeError: When the server does not become ready in time.
    """
    url = base_url.rstrip("/") + "/"
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(
        f"Demo UI not reachable at {url}. "
        "Start it with: docker compose up --build web-demo"
        + (f" (last error: {last_error})" if last_error else "")
    )


def _prepare_page_for_capture(page: object) -> None:
    """Stabilize fonts/animations and ensure no modal backdrop is open.

    Args:
        page (object): Playwright Page instance.
    """
    page.emulate_media(color_scheme="dark")
    page.evaluate(
        """() => {
            const style = document.getElementById('docs-screenshot-overrides');
            if (!style) {
                const el = document.createElement('style');
                el.id = 'docs-screenshot-overrides';
                el.textContent = `
                    *, *::before, *::after {
                        animation: none !important;
                        transition: none !important;
                        caret-color: transparent !important;
                    }
                    body, .main-content, .login-view {
                        background: #0e1016 !important;
                        overflow: hidden !important;
                    }
                    .card, .login-card, .form-card, .meta-card, .code-card {
                        background-color: #1a1e2e !important;
                        backdrop-filter: none !important;
                        -webkit-backdrop-filter: none !important;
                    }
                    .welcome-card {
                        max-width: 1100px !important;
                        margin: 24px auto !important;
                    }
                `;
                document.head.appendChild(el);
            }
            const modal = document.getElementById('db-details-modal');
            if (modal && typeof modal.close === 'function' && modal.open) {
                modal.close();
            }
            return document.fonts ? document.fonts.ready : Promise.resolve();
        }"""
    )
    page.wait_for_timeout(150)


def capture_screenshots(
    *,
    base_url: str,
    output_dir: Path,
    password: str,
) -> list[Path]:
    """Drive the demo UI and write README screenshot PNGs.

    Args:
        base_url (str): Demo web origin (e.g. http://127.0.0.1:8001).
        output_dir (Path): Directory for PNG output (docs/images).
        password (str): Shared login password from ASM_CLEANUP_PASSWORD.

    Returns:
        list[Path]: Absolute paths of written PNG files.

    Raises:
        RuntimeError: When Playwright is missing or UI elements are absent.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required. Install with: "
            "uv sync --group docs && uv run --group docs playwright install chromium"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=1,
            color_scheme="dark",
            reduced_motion="reduce",
        )
        page = context.new_page()

        page.goto(base_url.rstrip("/") + "/", wait_until="networkidle")
        page.locator("#view-login").wait_for(state="visible")
        _prepare_page_for_capture(page)
        path = output_dir / "01-login.png"
        page.screenshot(path=str(path), full_page=False)
        written.append(path.resolve())

        page.fill("#login-password", password)
        page.click("#login-form button[type='submit']")
        page.locator("#app-container").wait_for(state="visible")
        page.locator("#view-welcome").wait_for(state="visible")
        page.locator("#target-list li").first.wait_for(state="visible")
        _prepare_page_for_capture(page)
        path = output_dir / "02-dashboard.png"
        page.screenshot(path=str(path), full_page=False)
        written.append(path.resolve())

        page.click("#btn-add-target")
        page.locator("#view-target-form").wait_for(state="visible")
        _prepare_page_for_capture(page)
        path = output_dir / "03-add-connection.png"
        page.screenshot(path=str(path), full_page=False)
        written.append(path.resolve())

        page.click("#btn-cancel-form")
        page.locator("#view-welcome").wait_for(state="visible")

        completed = page.locator(
            "#scan-history-list .scan-item",
            has=page.locator(".badge-completed"),
        ).first
        completed.wait_for(state="visible")
        completed.click()
        page.locator("#view-scan-details").wait_for(state="visible")
        page.locator("#alias-table-body tr").first.wait_for(state="visible")
        # Increase viewport height to fit both results table and SQL section
        page.set_viewport_size({"width": 1920, "height": 1650})
        _prepare_page_for_capture(page)
        path = output_dir / "04-scan-results.png"
        page.screenshot(path=str(path), full_page=False)
        written.append(path.resolve())

        page.set_viewport_size(VIEWPORT)
        page.locator("#generated-sql-code").scroll_into_view_if_needed()
        _prepare_page_for_capture(page)
        path = output_dir / "05-generated-sql.png"
        page.screenshot(path=str(path), full_page=False)
        written.append(path.resolve())

        context.close()
        browser.close()

    return written


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and capture documentation screenshots.

    Args:
        argv (list[str] | None): Optional argument overrides.

    Returns:
        int: Process exit code.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Capture 16:9 docs screenshots from the web-demo service "
            "(default http://127.0.0.1:8001)."
        )
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Demo UI origin (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"PNG output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "Login password (default: ASM_CLEANUP_PASSWORD from the environment "
            "or repo .env, same value used by web-demo)"
        ),
    )
    args = parser.parse_args(argv)
    password = _resolve_login_password(args.password)

    if not password:
        sys.stderr.write(
            "Error: set ASM_CLEANUP_PASSWORD in .env (or the environment), "
            "or pass --password (same value used by web-demo).\n"
        )
        return 1

    try:
        _wait_for_server(args.base_url)
        paths = capture_screenshots(
            base_url=args.base_url,
            output_dir=args.output_dir,
            password=password,
        )
    except Exception as exc:  # noqa: BLE001 (CLI entrypoint: catch any error, log it to stderr and exit 1)
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    for path in paths:
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
