from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = PROJECT_ROOT / "frontend"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "deploy-pages.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_frontend_is_static_and_works_below_github_pages_subdirectory() -> None:
    index = _read(FRONTEND / "index.html")

    assert (FRONTEND / "styles.css").is_file()
    assert (FRONTEND / "app.js").is_file()
    assert (FRONTEND / "config.js").is_file()
    assert (FRONTEND / "assets").is_dir()
    assert 'href="./styles.css' in index
    assert 'src="./config.js' in index
    assert 'src="./app.js' in index
    assert 'href="./"' in index
    assert not re.search(r"""(?:src|href)=["']/""", index)
    assert "<base " not in index.lower()


def test_frontend_uses_configured_https_backend_without_fake_default() -> None:
    config = _read(FRONTEND / "config.js")
    app = _read(FRONTEND / "app.js")

    assert "window.AEP_CONFIG" in config
    assert 'API_BASE_URL: ""' in config
    assert "window.AEP_CONFIG?.API_BASE_URL" in app
    assert 'url.protocol !== "https:"' in app
    assert "apiBaseUrl}${normalizedPath}" in app
    assert 'fetch("/api/' not in app
    assert "fetch('/api/" not in app
    assert "backend.onrender.com" not in (config + app)


def test_frontend_downloads_blob_then_requests_explicit_job_deletion() -> None:
    app = _read(FRONTEND / "app.js")

    blob_position = app.index("await response.blob()")
    download_position = app.index("anchor.click()")
    deletion_position = app.index("const removed = await deleteJob(jobId)")
    assert blob_position < download_position < deletion_position
    assert 'method: "DELETE"' in app
    assert "URL.createObjectURL(blob)" in app
    assert "blob.size" in app
    assert "A limpeza automática por prazo permanece agendada." in app


def test_public_page_contains_accurate_privacy_notice_and_no_local_setup_copy() -> None:
    index = _read(FRONTEND / "index.html")
    visible_copy = re.sub(r"<[^>]+>", " ", index).casefold()

    assert (
        "os arquivos são utilizados somente durante a geração do documento. "
        "não há banco de dados ou armazenamento permanente. após o download, "
        "os arquivos da execução são excluídos automaticamente."
    ) in " ".join(visible_copy.split())
    for forbidden in (
        "powershell",
        "localhost",
        "iniciar.ps1",
        "ambiente virtual",
        "instalar python",
        "libreoffice local",
        "nenhum arquivo sai deste computador",
        "sem envio à nuvem",
        "processamento 100% local",
    ):
        assert forbidden not in visible_copy


def test_frontend_does_not_contain_credentials_or_private_documents() -> None:
    public_text = "\n".join(
        _read(path)
        for path in FRONTEND.rglob("*")
        if path.is_file() and path.name != ".gitkeep"
    )

    assert not re.search(
        r"(?i)(api[_-]?key|client[_-]?secret|access[_-]?token)\s*[:=]\s*['\"][^'\"]+",
        public_text,
    )
    assert "BEGIN PRIVATE KEY" not in public_text
    assert "private_templates" not in public_text
    assert "local_samples" not in public_text
    assert not re.search(r"(?i)[a-z]:\\", public_text)
    assert not any(
        path.suffix.lower() in {".doc", ".docx", ".xls", ".xlsx"}
        for path in FRONTEND.rglob("*")
    )


def test_pages_workflow_builds_config_and_publishes_frontend() -> None:
    workflow = _read(WORKFLOW)

    assert "push:" in workflow
    assert "- main" in workflow
    assert "contents: read" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "actions/checkout@v6" in workflow
    assert "actions/configure-pages@v5" in workflow
    assert "actions/upload-pages-artifact@v4" in workflow
    assert "actions/deploy-pages@v4" in workflow
    assert "AEP_API_BASE_URL: ${{ vars.AEP_API_BASE_URL }}" in workflow
    assert 'API_BASE_URL: ""' in workflow
    assert "path: frontend" in workflow
    assert "name: github-pages" in workflow
    assert "url: ${{ steps.deployment.outputs.page_url }}" in workflow
