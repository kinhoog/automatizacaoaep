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
    generator = _read(FRONTEND / "gerar.html")
    guide = _read(FRONTEND / "como-funciona.html")

    assert (FRONTEND / "styles.css").is_file()
    assert (FRONTEND / "app.js").is_file()
    assert (FRONTEND / "config.js").is_file()
    assert (FRONTEND / "gerar.html").is_file()
    assert (FRONTEND / "como-funciona.html").is_file()
    assert (FRONTEND / "assets").is_dir()
    assert 'href="./styles.css' in index
    assert 'src="./config.js' not in index
    assert 'src="./app.js' not in index
    assert 'href="./gerar.html"' in index
    assert 'href="./como-funciona.html"' in index
    assert 'href="./"' in index
    assert 'src="./config.js' in generator
    assert 'src="./app.js' in generator
    assert 'href="./como-funciona.html"' in generator
    assert 'href="./styles.css' in guide
    assert 'href="./"' in guide
    assert 'href="./gerar.html"' in guide
    for page in (index, generator, guide):
        assert not re.search(r"""(?:src|href)=["']/""", page)
        assert "<base " not in page.lower()


def test_landing_generator_and_guide_are_separate_focused_pages() -> None:
    index = _read(FRONTEND / "index.html")
    generator = _read(FRONTEND / "gerar.html")
    guide = _read(FRONTEND / "como-funciona.html")
    guide_copy = " ".join(re.sub(r"<[^>]+>", " ", guide).casefold().split())

    assert '<body class="landing-page">' in index
    assert '<main id="conteudo" class="minimal-landing">' in index
    assert "Dos relatórios aprovados" in index
    assert 'href="./gerar.html"' in index
    assert 'id="aep-form"' not in index
    assert 'class="stepper"' not in index

    assert '<body class="workspace-page">' in generator
    assert '<main id="conteudo" class="document-workspace">' in generator
    assert 'id="aep-form"' in generator
    assert 'class="stepper"' in generator
    assert 'class="app-intro"' not in generator
    assert "Menos montagem." not in generator

    assert "Como funciona" in guide
    for stage in ("envie", "valide", "reconcilie", "gere"):
        assert stage in guide_copy
    assert 'class="process-list"' in guide
    assert "guide-card" not in guide
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


def test_frontend_aborts_hung_requests_and_stops_repeated_poll_failures() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js não está disponível para testar timeout do frontend.")

    app_path = FRONTEND / "app.js"
    app = _read(app_path)
    assert "const MAX_POLL_FAILURES = 5" in app
    assert "function stopPolling(message, epoch, jobId)" in app
    assert "state.pollFailures >= MAX_POLL_FAILURES" in app
    assert "para evitar um carregamento infinito" in app

    script = r"""
const fs = require("fs");
global.window = {
  AEP_CONFIG: { API_BASE_URL: "https://api.example.test" },
  setTimeout,
  clearTimeout,
};
global.fetch = (_url, options) =>
  new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => {
      const error = new Error("aborted");
      error.name = "AbortError";
      reject(error);
    });
  });

const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("const REQUEST_TIMEOUT_MS");
const end = source.indexOf("async function deleteJob");
if (start < 0 || end <= start) throw new Error("Helpers de timeout ausentes.");
eval(
  source.slice(start, end) +
    ";globalThis.apiFetch = apiFetch;globalThis.ApiError = ApiError;",
);

(async () => {
  const started = Date.now();
  let observed;
  try {
    await globalThis.apiFetch("/api/test", {}, 15);
  } catch (error) {
    observed = error;
  }
  if (!(observed instanceof globalThis.ApiError)) {
    throw new Error("Timeout não produziu ApiError.");
  }
  if (observed.status !== 408) throw new Error("Status de timeout incorreto.");
  if (Date.now() - started > 500) throw new Error("Abort demorou demais.");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    completed = subprocess.run(
        [node, "-e", script, str(app_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
    )

    assert completed.returncode == 0, completed.stderr


def test_frontend_ignores_stale_poll_and_recovers_lost_generate_response() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js não está disponível para testar o polling frontend.")

    app_path = FRONTEND / "app.js"
    app = _read(app_path)
    assert "function isCurrentPoll(epoch, jobId)" in app
    assert "async function pollJob(epoch, jobId)" in app
    assert "await recoverGenerationAfterStartFailure(jobId)" in app

    script = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("function isCurrentPoll");
const end = source.indexOf("function showCompleted");
if (start < 0 || end <= start) throw new Error("Helpers de polling ausentes.");

const REQUEST_TIMEOUT_MS = { polling: 15 };
const MAX_POLL_FAILURES = 5;
class ApiError extends Error {
  constructor(message, fields = {}, status = 0) {
    super(message);
    this.fields = fields;
    this.status = status;
  }
}
const state = {
  jobId: "job-antigo",
  pollEpoch: 7,
  pollTimer: null,
  pollStartedAt: Date.now(),
  pollFailures: 0,
};
let fetchImplementation;
async function apiFetch(...args) {
  return fetchImplementation(...args);
}
async function parseResponse(response) {
  return response;
}
let scheduled = [];
const window = {
  setTimeout(callback, delay) {
    scheduled.push({ callback, delay });
    return scheduled.length;
  },
  clearTimeout() {},
};
let progressUpdates = 0;
let processingSnapshots = [];
let completedSnapshots = [];
function updateProgress() {
  progressUpdates += 1;
}
function showProcessing(snapshot) {
  processingSnapshots.push(snapshot);
}
function showCompleted(snapshot) {
  completedSnapshots.push(snapshot);
}
function setStep() {}
function showError() {}

eval(
  source.slice(start, end)
    + ";globalThis.pollJob = pollJob;"
    + "globalThis.recoverGenerationAfterStartFailure = "
    + "recoverGenerationAfterStartFailure;",
);

(async () => {
  let releaseOldFetch;
  fetchImplementation = () =>
    new Promise((resolve) => {
      releaseOldFetch = resolve;
    });
  const oldPoll = globalThis.pollJob(7, "job-antigo");
  await Promise.resolve();
  state.pollEpoch = 8;
  state.jobId = "job-novo";
  releaseOldFetch({
    status: "generating",
    progress: 70,
    stage: "resposta obsoleta",
  });
  await oldPoll;
  if (progressUpdates !== 0) {
    throw new Error("Polling obsoleto atualizou o progresso.");
  }
  if (scheduled.length !== 0) {
    throw new Error("Polling obsoleto reagendou uma nova consulta.");
  }

  state.jobId = "job-recuperado";
  state.pollTimer = null;
  state.pollStartedAt = null;
  state.pollFailures = 0;
  scheduled = [];
  fetchImplementation = async () => ({
    status: "generating",
    progress: 60,
    stage: "geração recuperada",
  });
  const recovered =
    await globalThis.recoverGenerationAfterStartFailure("job-recuperado");
  if (!recovered) throw new Error("Geração aceita não foi recuperada.");
  if (processingSnapshots.length !== 1) {
    throw new Error("Tela de processamento não foi restaurada.");
  }
  if (scheduled.length !== 1 || scheduled[0].delay !== 500) {
    throw new Error("Polling recuperado não foi agendado corretamente.");
  }
  if (completedSnapshots.length !== 0) {
    throw new Error("Geração em andamento foi tratada como concluída.");
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    completed = subprocess.run(
        [node, "-e", script, str(app_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
    )

    assert completed.returncode == 0, completed.stderr


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
    generator = _read(FRONTEND / "gerar.html")
    guide = _read(FRONTEND / "como-funciona.html")
    visible_copy = re.sub(r"<[^>]+>", " ", index + generator + guide).casefold()

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
