from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = PROJECT_ROOT / "frontend"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "deploy-pages.yml"
SYNTHETIC_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "public_synthetic"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_frontend_is_static_and_works_below_github_pages_subdirectory() -> None:
    index = _read(FRONTEND / "index.html")
    guide = _read(FRONTEND / "como-funciona.html")

    assert (FRONTEND / "styles.css").is_file()
    assert (FRONTEND / "app.js").is_file()
    assert (FRONTEND / "config.js").is_file()
    assert (FRONTEND / "como-funciona.html").is_file()
    assert (FRONTEND / "assets").is_dir()
    assert 'href="./styles.css' in index
    assert 'src="./config.js' in index
    assert 'src="./app.js' in index
    assert 'href="./como-funciona.html"' in index
    assert 'href="./"' in index
    assert 'href="./styles.css' in guide
    assert 'href="./"' in guide
    for page in (index, guide):
        assert not re.search(r"""(?:src|href)=["']/""", page)
        assert "<base " not in page.lower()


def test_main_page_starts_with_the_form_and_guide_is_a_separate_page() -> None:
    index = _read(FRONTEND / "index.html")
    guide = _read(FRONTEND / "como-funciona.html")
    guide_copy = " ".join(re.sub(r"<[^>]+>", " ", guide).casefold().split())

    assert '<main id="conteudo">' in index
    assert '<div class="shell workspace">' in index
    assert '<section class="hero">' not in index
    assert 'class="process-card"' not in index
    assert "Etapas do processo" not in index
    assert index.index('<div class="shell workspace">') < index.index('id="aep-form"')

    assert "Como funciona" in guide
    for stage in ("envie", "valide", "reconcilie", "gere"):
        assert stage in guide_copy
    assert "app.js" not in guide
    assert "config.js" not in guide
    assert "processamento temporário" in guide_copy


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
    validation_position = app.index("await validateArtifact(response, blob, kind)")
    download_position = app.index("anchor.click()")
    deletion_position = app.index("const removed = await deleteJob(jobId)")
    assert blob_position < validation_position < download_position < deletion_position
    assert 'method: "DELETE"' in app
    assert "URL.createObjectURL(blob)" in app
    assert "blob.size" in app
    assert "A limpeza automática por prazo permanece agendada." in app


def test_frontend_validates_artifacts_without_transport_content_length() -> None:
    app = _read(FRONTEND / "app.js")

    assert 'headers.get("Content-Length")' not in app
    assert 'headers.get("X-AEP-Content-Length")' in app
    assert 'headers.get("X-AEP-Content-SHA256")' in app
    assert "window.crypto.subtle.digest(" in app
    assert '"SHA-256"' in app
    assert "[0x50, 0x4b, 0x05, 0x06]" in app
    assert 'entryNames.has("[Content_Types].xml")' in app
    assert 'entryNames.has("word/document.xml")' in app
    assert "JSON.parse(await blob.text())" in app


def test_frontend_serializes_document_and_report_downloads() -> None:
    app = _read(FRONTEND / "app.js")

    lock_check = app.index("if (state.downloadInProgress)")
    lock_set = app.index("state.downloadInProgress = true", lock_check)
    receive = app.index("await receiveArtifact(", lock_set)
    unlock = app.index("state.downloadInProgress = false", receive)

    assert lock_check < lock_set < receive < unlock
    assert "updateDownloadControls();" in app[lock_set:receive]
    assert "clearErrors();" in app[lock_set:receive]


def test_frontend_artifact_validation_with_public_synthetic_fixture() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js não está disponível para executar a validação frontend.")

    script = r"""
const crypto = require("crypto");
const fs = require("fs");
global.window = { crypto: crypto.webcrypto };

const app = fs.readFileSync(process.argv[1], "utf8");
const helperStart = app.indexOf("function incompleteArtifactError");
const helperEnd = app.indexOf("function extensionAccepted");
if (helperStart < 0 || helperEnd <= helperStart) {
  throw new Error("Não foi possível localizar os validadores de artefato.");
}

class ApiError extends Error {}
eval(
  app.slice(helperStart, helperEnd) +
    ";globalThis.validateArtifact = validateArtifact;",
);

function responseHeaders(values) {
  const normalized = new Map(
    Object.entries(values).map(([name, value]) => [name.toLowerCase(), value]),
  );
  return {
    headers: {
      get(name) {
        return normalized.get(name.toLowerCase()) ?? null;
      },
    },
  };
}

(async () => {
  const source = fs.readFileSync(process.argv[2]);
  const blob = new Blob([source]);
  const integrity = responseHeaders({
    "X-AEP-Content-Length": String(source.length),
    "X-AEP-Content-SHA256": crypto
      .createHash("sha256")
      .update(source)
      .digest("hex"),
  });
  await globalThis.validateArtifact(integrity, blob, "document");

  const transportLengthOnly = responseHeaders({ "Content-Length": "1" });
  await globalThis.validateArtifact(transportLengthOnly, blob, "document");

  let truncatedRejected = false;
  try {
    const truncated = source.subarray(0, source.length - 8);
    await globalThis.validateArtifact(
      responseHeaders({}),
      new Blob([truncated]),
      "document",
    );
  } catch {
    truncatedRejected = true;
  }
  if (!truncatedRejected) throw new Error("DOCX truncado foi aceito.");

  let badHashRejected = false;
  try {
    await globalThis.validateArtifact(
      responseHeaders({
        "X-AEP-Content-Length": String(source.length),
        "X-AEP-Content-SHA256": "0".repeat(64),
      }),
      blob,
      "document",
    );
  } catch {
    badHashRejected = true;
  }
  if (!badHashRejected) throw new Error("Hash divergente foi aceito.");

  await globalThis.validateArtifact(
    responseHeaders({}),
    new Blob([JSON.stringify({ synthetic: true, validity: "none" })]),
    "report",
  );
  let invalidJsonRejected = false;
  try {
    await globalThis.validateArtifact(
      responseHeaders({}),
      new Blob(["{"]),
      "report",
    );
  } catch {
    invalidJsonRejected = true;
  }
  if (!invalidJsonRejected) throw new Error("JSON inválido foi aceito.");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    completed = subprocess.run(
        [
            node,
            "-e",
            script,
            str(FRONTEND / "app.js"),
            str(SYNTHETIC_FIXTURES / "template_aep_sintetico.docx"),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr


def test_public_page_contains_accurate_privacy_notice_and_no_local_setup_copy() -> None:
    index = _read(FRONTEND / "index.html")
    guide = _read(FRONTEND / "como-funciona.html")
    visible_copy = re.sub(r"<[^>]+>", " ", index + guide).casefold()

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
    assert 'fs.readdirSync("frontend")' in workflow
    assert 'file.endsWith(".html")' in workflow
    assert "path: frontend" in workflow
    assert "name: github-pages" in workflow
    assert "url: ${{ steps.deployment.outputs.page_url }}" in workflow
