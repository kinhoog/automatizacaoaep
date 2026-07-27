(() => {
  "use strict";

  const form = document.querySelector("#aep-form");
  const validateButton = document.querySelector("#validate-button");
  const generateButton = document.querySelector("#generate-button");
  const processingPanel = document.querySelector("#processing-panel");
  const progressBar = document.querySelector("#progress-bar");
  const progressTrack = document.querySelector(".progress-track");
  const progressValue = document.querySelector("#progress-value");
  const processingStage = document.querySelector("#processing-stage");
  const integratedUploads = document.querySelector("#integrated-uploads");
  const separateUploads = document.querySelector("#separate-uploads");
  const reconciliationBlock = document.querySelector("#reconciliation-block");
  const reconciliationList = document.querySelector("#reconciliation-list");
  const warningsBlock = document.querySelector("#warnings-block");
  const warningList = document.querySelector("#warning-list");
  const errorPanel = document.querySelector("#error-panel");
  const errorMessage = document.querySelector("#error-message");
  const errorFields = document.querySelector("#error-fields");
  const earlyReportLink = document.querySelector("#early-report-link");
  const documentLink = document.querySelector("#document-link");
  const reportLink = document.querySelector("#report-link");
  const compatibilityMode = document.querySelector("#compatibility-mode");
  const cleanupStatus = document.querySelector("#cleanup-status");

  const state = {
    step: 1,
    jobId: null,
    validation: null,
    pollTimer: null,
    pollStartedAt: null,
    uploadCount: 0,
    downloadInProgress: false,
    downloads: {
      document: false,
      validationReport: false,
    },
  };

  const fieldNames = {
    company_name: "Razão social",
    competence: "Competência",
    ergo_reference_date: "Data-base do Ergo",
    psychosocial_reference_date: "Data-base do psicossocial",
    ghe_spreadsheet: "Planilha oficial dos GHEs",
    psychosocial_report: "Relatório psicossocial bruto",
    ergo_report: "Relatório Ergo bruto",
    integrated_report: "Relatório técnico integrado",
    psychosocial_analysis: "Análise psicossocial",
    ergonomic_analysis: "Análise ergonômica",
    cnpj_card: "Imagem do cartão CNPJ",
    company_logo: "Logo da empresa",
  };

  class ApiError extends Error {
    constructor(message, fields = {}, status = 0) {
      super(message);
      this.name = "ApiError";
      this.fields = fields || {};
      this.status = status;
    }
  }

  function configuredApiBaseUrl() {
    const value = String(window.AEP_CONFIG?.API_BASE_URL || "").trim();
    if (!value) return null;
    try {
      const url = new URL(value);
      if (
        url.protocol !== "https:" ||
        url.username ||
        url.password ||
        url.search ||
        url.hash
      ) {
        return null;
      }
      return value.replace(/\/+$/, "");
    } catch {
      return null;
    }
  }

  const apiBaseUrl = configuredApiBaseUrl();

  async function apiFetch(path, options = {}) {
    if (!apiBaseUrl) {
      throw new ApiError(
        "O serviço de processamento ainda não foi configurado para esta página.",
      );
    }
    const normalizedPath = path.startsWith("/") ? path : `/${path}`;
    return fetch(`${apiBaseUrl}${normalizedPath}`, {
      mode: "cors",
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      ...options,
    });
  }

  async function deleteJob(jobId) {
    if (!jobId || !apiBaseUrl) return false;
    try {
      const response = await apiFetch(
        `/api/jobs/${encodeURIComponent(jobId)}`,
        {
          method: "DELETE",
          headers: { Accept: "application/json" },
        },
      );
      return response.ok || response.status === 404;
    } catch {
      return false;
    }
  }

  function setStep(step) {
    state.step = step;
    document.querySelectorAll("[data-step]").forEach((panel) => {
      panel.hidden = Number(panel.dataset.step) !== step;
    });
    processingPanel.hidden = true;

    document.querySelectorAll("[data-step-link]").forEach((button) => {
      const buttonStep = Number(button.dataset.stepLink);
      button.classList.toggle("is-active", buttonStep === step);
      button.classList.toggle("is-complete", buttonStep < step);
      button.toggleAttribute("disabled", buttonStep > step);
      if (buttonStep === step) {
        button.setAttribute("aria-current", "step");
      } else {
        button.removeAttribute("aria-current");
      }
    });

    const activePanel = document.querySelector(`[data-step="${step}"]`);
    if (activePanel) {
      activePanel.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function showProcessing(snapshot = {}) {
    document.querySelectorAll("[data-step]").forEach((panel) => {
      panel.hidden = true;
    });
    processingPanel.hidden = false;
    document.querySelectorAll("[data-step-link]").forEach((button) => {
      const buttonStep = Number(button.dataset.stepLink);
      button.classList.toggle("is-active", buttonStep === 4);
      button.classList.toggle("is-complete", buttonStep < 4);
      button.removeAttribute("aria-current");
    });
    updateProgress(snapshot);
    processingPanel.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function updateProgress(snapshot = {}) {
    const progress = Math.max(0, Math.min(100, Number(snapshot.progress || 0)));
    const stage = snapshot.stage || "Processando documentos…";
    progressBar.style.width = `${progress}%`;
    progressValue.textContent = `${progress}%`;
    progressTrack.setAttribute("aria-valuenow", String(progress));
    processingStage.textContent = stage;
  }

  function clearErrors() {
    errorPanel.hidden = true;
    errorMessage.textContent = "";
    errorFields.replaceChildren();
    document.querySelectorAll("[aria-invalid='true']").forEach((element) => {
      element.removeAttribute("aria-invalid");
    });
    document.querySelectorAll(".upload-card.has-error").forEach((card) => {
      card.classList.remove("has-error");
    });
    document.querySelectorAll(".field-error").forEach((element) => {
      element.textContent = "";
    });
  }

  function showError(message, fields = {}) {
    errorMessage.textContent =
      message || "Ocorreu um erro inesperado. Tente novamente.";
    errorFields.replaceChildren();

    Object.entries(fields || {}).forEach(([name, detail]) => {
      const input = form.querySelector(`[name="${CSS.escape(name)}"]`);
      if (input) {
        input.setAttribute("aria-invalid", "true");
        const card = input.closest("[data-upload-card]");
        if (card) {
          card.classList.add("has-error");
        }
      }
      const errorTarget = document.querySelector(
        `#${CSS.escape(name.replaceAll("_", "-"))}-error`,
      );
      if (errorTarget) {
        errorTarget.textContent = String(detail);
      }
      const item = document.createElement("li");
      item.textContent = `${fieldNames[name] || name}: ${String(detail)}`;
      errorFields.append(item);
    });

    errorPanel.hidden = false;
    errorPanel.focus?.();
  }

  function setButtonBusy(button, busy, label) {
    if (!button.dataset.originalHtml) {
      button.dataset.originalHtml = button.innerHTML;
    }
    button.disabled = busy;
    button.classList.toggle("is-loading", busy);
    button.setAttribute("aria-busy", String(busy));
    if (busy) {
      button.textContent = label;
    } else {
      button.innerHTML = button.dataset.originalHtml;
    }
  }

  function invalidateValidation() {
    if (!state.jobId && !state.validation) return;
    const obsoleteJobId = state.jobId;
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
    }
    state.jobId = null;
    state.validation = null;
    state.pollTimer = null;
    state.pollStartedAt = null;
    state.uploadCount = 0;
    state.downloads.document = false;
    state.downloads.validationReport = false;
    reconciliationList.replaceChildren();
    reconciliationBlock.hidden = true;
    generateButton.disabled = true;
    earlyReportLink.hidden = true;
    earlyReportLink.href = "#";
    for (const link of [documentLink, reportLink]) {
      link.href = "#";
      link.setAttribute("aria-disabled", "true");
    }
    document.querySelector("[data-step-link='3']").disabled = true;
    document.querySelector("[data-step-link='4']").disabled = true;
    cleanupStatus.textContent = "";
    if (obsoleteJobId) {
      void deleteJob(obsoleteJobId);
    }
  }

  function modeValue() {
    return form.querySelector("[name='analysis_mode']:checked")?.value || "integrated";
  }

  function updateAnalysisMode() {
    const integrated = modeValue() === "integrated";
    integratedUploads.hidden = !integrated;
    separateUploads.hidden = integrated;
    const integratedInput = form.querySelector("[name='integrated_report']");
    const psychosocialInput = form.querySelector("[name='psychosocial_analysis']");
    const ergonomicInput = form.querySelector("[name='ergonomic_analysis']");
    integratedInput.required = integrated;
    psychosocialInput.required = !integrated;
    ergonomicInput.required = !integrated;
    clearErrors();
  }

  function readableSize(size) {
    if (!Number.isFinite(size)) return "";
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
  }

  function incompleteArtifactError() {
    return new ApiError("O arquivo recebido está incompleto. Tente novamente.");
  }

  function bytesMatch(bytes, offset, expected) {
    return expected.every((value, index) => bytes[offset + index] === value);
  }

  async function validateDocxBlob(blob) {
    if (blob.size < 22) {
      throw incompleteArtifactError();
    }

    const localHeader = new Uint8Array(
      await blob.slice(0, 4).arrayBuffer(),
    );
    if (!bytesMatch(localHeader, 0, [0x50, 0x4b, 0x03, 0x04])) {
      throw incompleteArtifactError();
    }

    const maximumEndRecordSize = 22 + 0xffff;
    const tailOffset = Math.max(0, blob.size - maximumEndRecordSize);
    const tail = new Uint8Array(await blob.slice(tailOffset).arrayBuffer());
    const view = new DataView(tail.buffer, tail.byteOffset, tail.byteLength);
    let centralDirectory = null;

    for (let offset = tail.length - 22; offset >= 0; offset -= 1) {
      if (!bytesMatch(tail, offset, [0x50, 0x4b, 0x05, 0x06])) {
        continue;
      }
      const commentLength = view.getUint16(offset + 20, true);
      if (offset + 22 + commentLength !== tail.length) {
        continue;
      }

      const diskNumber = view.getUint16(offset + 4, true);
      const centralDirectoryDisk = view.getUint16(offset + 6, true);
      const diskEntryCount = view.getUint16(offset + 8, true);
      const totalEntryCount = view.getUint16(offset + 10, true);
      const size = view.getUint32(offset + 12, true);
      const start = view.getUint32(offset + 16, true);
      const endRecordOffset = tailOffset + offset;
      if (
        diskNumber !== 0 ||
        centralDirectoryDisk !== 0 ||
        diskEntryCount === 0 ||
        diskEntryCount !== totalEntryCount ||
        start + size > endRecordOffset
      ) {
        continue;
      }
      centralDirectory = { start, size, totalEntryCount };
      break;
    }

    if (!centralDirectory) {
      throw incompleteArtifactError();
    }

    const directoryBytes = new Uint8Array(
      await blob
        .slice(
          centralDirectory.start,
          centralDirectory.start + centralDirectory.size,
        )
        .arrayBuffer(),
    );
    const directoryView = new DataView(
      directoryBytes.buffer,
      directoryBytes.byteOffset,
      directoryBytes.byteLength,
    );
    const entryNames = new Set();
    let entryOffset = 0;
    let parsedEntries = 0;
    while (entryOffset + 46 <= directoryBytes.length) {
      if (
        !bytesMatch(directoryBytes, entryOffset, [0x50, 0x4b, 0x01, 0x02])
      ) {
        throw incompleteArtifactError();
      }
      const nameLength = directoryView.getUint16(entryOffset + 28, true);
      const extraLength = directoryView.getUint16(entryOffset + 30, true);
      const commentLength = directoryView.getUint16(entryOffset + 32, true);
      const nextOffset =
        entryOffset + 46 + nameLength + extraLength + commentLength;
      if (nextOffset > directoryBytes.length) {
        throw incompleteArtifactError();
      }
      entryNames.add(
        new TextDecoder().decode(
          directoryBytes.slice(entryOffset + 46, entryOffset + 46 + nameLength),
        ),
      );
      parsedEntries += 1;
      entryOffset = nextOffset;
    }

    if (
      entryOffset !== directoryBytes.length ||
      parsedEntries !== centralDirectory.totalEntryCount ||
      !entryNames.has("[Content_Types].xml") ||
      !entryNames.has("word/document.xml")
    ) {
      throw incompleteArtifactError();
    }
  }

  async function validateJsonBlob(blob) {
    try {
      const parsed = JSON.parse(await blob.text());
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw incompleteArtifactError();
      }
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw incompleteArtifactError();
    }
  }

  async function sha256Hex(blob) {
    if (!window.crypto?.subtle) {
      throw new ApiError(
        "Este navegador não conseguiu verificar a integridade do arquivo.",
      );
    }
    const digest = await window.crypto.subtle.digest(
      "SHA-256",
      await blob.arrayBuffer(),
    );
    return Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
  }

  async function validateArtifact(response, blob, kind) {
    if (!blob.size) {
      throw incompleteArtifactError();
    }

    const expectedSizeHeader = response.headers.get("X-AEP-Content-Length");
    if (expectedSizeHeader !== null) {
      const normalizedSize = expectedSizeHeader.trim();
      const expectedSize = Number(normalizedSize);
      if (
        !/^\d+$/.test(normalizedSize) ||
        !Number.isSafeInteger(expectedSize) ||
        expectedSize !== blob.size
      ) {
        throw incompleteArtifactError();
      }
    }

    const expectedHashHeader = response.headers.get("X-AEP-Content-SHA256");
    if (expectedHashHeader !== null) {
      const expectedHash = expectedHashHeader.trim().toLowerCase();
      if (
        !/^[a-f0-9]{64}$/.test(expectedHash) ||
        (await sha256Hex(blob)) !== expectedHash
      ) {
        throw incompleteArtifactError();
      }
    }

    if (kind === "document") {
      await validateDocxBlob(blob);
    } else {
      await validateJsonBlob(blob);
    }
  }

  function updateDownloadControls() {
    const controls = [
      [documentLink, state.downloads.document],
      [reportLink, state.downloads.validationReport],
      [earlyReportLink, state.downloads.validationReport],
    ];
    controls.forEach(([control, available]) => {
      const disabled = state.downloadInProgress || !available;
      control.setAttribute("aria-disabled", String(disabled));
      if (control === documentLink || control === reportLink) {
        control.classList.toggle("is-disabled", disabled);
      }
    });
  }

  function extensionAccepted(input, file) {
    const accepted = (input.accept || "")
      .split(",")
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean);
    if (!accepted.length) return true;
    const lowerName = file.name.toLowerCase();
    return accepted.some((extension) => lowerName.endsWith(extension));
  }

  function updateUploadCard(input) {
    const card = input.closest("[data-upload-card]");
    const label = card.querySelector("[data-file-label]");
    const removeButton = card.querySelector("[data-remove-file]");
    const file = input.files?.[0];
    card.classList.toggle("has-file", Boolean(file));
    card.classList.remove("has-error");
    removeButton.hidden = !file;
    label.textContent = file
      ? `${file.name} · ${readableSize(file.size)}`
      : "Nenhum arquivo selecionado";
  }

  function validateIdentification() {
    clearErrors();
    const required = [
      "company_name",
      "competence",
      "ergo_reference_date",
      "psychosocial_reference_date",
    ];
    const fields = {};
    required.forEach((name) => {
      const input = form.querySelector(`[name="${name}"]`);
      if (!input.value.trim()) {
        fields[name] = "campo obrigatório";
      } else if (!input.checkValidity()) {
        fields[name] = "valor inválido";
      }
    });
    if (Object.keys(fields).length) {
      showError("Preencha os dados obrigatórios para continuar.", fields);
      return false;
    }
    return true;
  }

  function requiredUploadNames() {
    const common = [
      "ghe_spreadsheet",
      "psychosocial_report",
      "ergo_report",
      "cnpj_card",
    ];
    if (modeValue() === "integrated") {
      return [...common, "integrated_report"];
    }
    return [...common, "psychosocial_analysis", "ergonomic_analysis"];
  }

  function validateUploads() {
    clearErrors();
    const fields = {};
    requiredUploadNames().forEach((name) => {
      const input = form.querySelector(`[name="${name}"]`);
      const file = input.files?.[0];
      if (!file) {
        fields[name] = "arquivo obrigatório";
      } else if (!extensionAccepted(input, file)) {
        fields[name] = "formato não aceito";
      } else if (file.size > 50 * 1024 * 1024) {
        fields[name] = "o arquivo excede 50 MB";
      }
    });

    form.querySelectorAll("input[type='file']").forEach((input) => {
      const file = input.files?.[0];
      if (file && !extensionAccepted(input, file)) {
        fields[input.name] = "formato não aceito";
      }
    });

    if (Object.keys(fields).length) {
      showError("Revise os arquivos obrigatórios.", fields);
      return false;
    }
    return true;
  }

  async function parseResponse(response) {
    let data;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (!response.ok) {
      const detail = data?.detail;
      if (Array.isArray(detail)) {
        throw new ApiError(
          "Alguns dados enviados são inválidos.",
          Object.fromEntries(
            detail.map((item) => [
              item.loc?.at(-1) || "form",
              item.msg || "valor inválido",
            ]),
          ),
          response.status,
        );
      }
      throw new ApiError(
        detail?.message || (typeof detail === "string" ? detail : null) ||
          "A solicitação não pôde ser concluída.",
        detail?.fields || {},
        response.status,
      );
    }
    return data;
  }

  function textList(value) {
    if (Array.isArray(value)) {
      return value
        .map((item) => {
          if (typeof item === "string") return item;
          return item?.name || item?.nome || item?.title || "";
        })
        .filter(Boolean)
        .join(", ");
    }
    if (value && typeof value === "object") {
      return Object.values(value).filter(Boolean).join(", ");
    }
    return String(value || "—");
  }

  function appendCell(row, value, className = "") {
    const cell = document.createElement("td");
    cell.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
    if (className) cell.className = className;
    row.append(cell);
  }

  function renderGhes(summary = {}) {
    const ghes = Array.isArray(summary.ghes) ? summary.ghes : [];
    const tbody = document.querySelector("#ghe-table-body");
    tbody.replaceChildren();

    ghes.forEach((ghe) => {
      const row = document.createElement("tr");
      appendCell(row, ghe.code || ghe.id);
      appendCell(row, ghe.name);
      appendCell(row, textList(ghe.sectors));
      appendCell(row, textList(ghe.roles));
      appendCell(row, Number(ghe.employees || 0), "cell-number");
      tbody.append(row);
    });

    if (!ghes.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.textContent = "Nenhum GHE foi retornado pela validação.";
      row.append(cell);
      tbody.append(row);
    }

    document.querySelector("#ghe-count").textContent = String(
      summary.ghe_count ?? ghes.length,
    );
    document.querySelector("#population-count").textContent = String(
      summary.total_population ?? 0,
    );
  }

  function renderWarnings(warnings = [], errors = []) {
    const messages = [...(warnings || []), ...(errors || [])];
    warningList.replaceChildren();
    messages.forEach((warning) => {
      const item = document.createElement("li");
      item.textContent =
        typeof warning === "string"
          ? warning
          : warning.message || warning.detail || warning.code || "Aviso de validação";
      warningList.append(item);
    });
    warningsBlock.hidden = messages.length === 0;
    document.querySelector("#warning-count").textContent = String(messages.length);
  }

  function reconciliationItems(reconciliation) {
    if (Array.isArray(reconciliation)) return reconciliation;
    if (!reconciliation || typeof reconciliation !== "object") return [];
    const candidates =
      reconciliation.items ||
      reconciliation.entries ||
      reconciliation.mappings ||
      reconciliation.decisions ||
      [];
    return Array.isArray(candidates)
      ? candidates
      : candidates && typeof candidates === "object"
        ? Object.values(candidates)
        : [];
  }

  function sourceIdentity(item, index) {
    const source =
      item.source ||
      item.ergo ||
      item.ergo_ghe ||
      item.source_ghe ||
      item;
    return {
      id: String(
        source.id ||
          source.identifier ||
          source.code ||
          item.ergo_id ||
          item.source_id ||
          `ergo-${index + 1}`,
      ),
      code: String(
        source.code ||
          source.codigo ||
          item.ergo_code ||
          item.source_code ||
          "",
      ),
      name: String(
        source.name ||
          source.nome ||
          source.title ||
          item.ergo_name ||
          item.source_name ||
          "",
      ),
    };
  }

  function officialCandidates(item, summary) {
    const candidates = [];
    for (const values of [
      item.candidates,
      item.official_candidates,
      item.possible_matches,
      summary.ghes,
    ]) {
      if (Array.isArray(values)) candidates.push(...values);
    }
    return candidates;
  }

  function candidateIdentity(candidate) {
    const source = candidate.official_ghe || candidate.ghe || candidate;
    return {
      id: String(
        source.id ||
          source.official_ghe_code ||
          source.code ||
          source.codigo ||
          source.name ||
          "",
      ),
      code: String(
        source.official_ghe_code ||
          source.code ||
          source.codigo ||
          source.id ||
          "",
      ),
      name: String(
        source.official_ghe_name ||
          source.name ||
          source.nome ||
          "",
      ),
    };
  }

  function suggestedCandidate(item) {
    const source =
      item.suggested ||
      item.suggestion ||
      item.suggested_match ||
      item.official_ghe ||
      {};
    return String(
      item.suggested_official_id ||
        item.suggested_ghe_id ||
        item.target_id ||
        item.official_ghe_code ||
        source.id ||
        source.code ||
        "",
    );
  }

  function renderReconciliation(reconciliation, summary) {
    const items = reconciliationItems(reconciliation);
    reconciliationList.replaceChildren();

    items.forEach((item, index) => {
      const source = sourceIdentity(item, index);
      const row = document.createElement("div");
      row.className = "reconciliation-row";

      const sourceBlock = document.createElement("div");
      sourceBlock.className = "source-ghe";
      const title = document.createElement("strong");
      title.textContent = [source.code, source.name].filter(Boolean).join(" — ") || source.id;
      const detail = document.createElement("small");
      detail.textContent = "Bloco identificado no relatório Ergo";
      sourceBlock.append(title, detail);

      const choice = document.createElement("div");
      choice.className = "reconciliation-choice";
      const select = document.createElement("select");
      select.dataset.sourceId = source.id;
      select.dataset.sourceCode = source.code;
      select.dataset.sourceName = source.name;
      select.setAttribute("aria-label", `Decisão para ${title.textContent}`);

      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Selecione uma correspondência";
      select.append(placeholder);

      const candidateIds = new Set();
      officialCandidates(item, summary).forEach((candidate) => {
        const normalized = candidateIdentity(candidate);
        if (!normalized.id || candidateIds.has(normalized.id)) return;
        candidateIds.add(normalized.id);
        const option = document.createElement("option");
        option.value = normalized.id;
        option.textContent =
          [normalized.code, normalized.name].filter(Boolean).join(" — ") ||
          normalized.id;
        select.append(option);
      });

      const notApplicable = document.createElement("option");
      notApplicable.value = "__not_applicable__";
      notApplicable.textContent = "Não aplicável — manter registrado";
      select.append(notApplicable);

      const suggested = suggestedCandidate(item);
      if (suggested && [...select.options].some((option) => option.value === suggested)) {
        select.value = suggested;
      } else if (
        item.not_applicable === true ||
        item.status === "not_applicable"
      ) {
        select.value = "__not_applicable__";
      }

      select.addEventListener("change", () => {
        select.removeAttribute("aria-invalid");
        clearErrors();
      });
      choice.append(select);
      row.append(sourceBlock, choice);
      reconciliationList.append(row);
    });

    reconciliationBlock.hidden = items.length === 0;
    return items.length;
  }

  function updateValidationStatus(snapshot, reconciliationCount) {
    const badge = document.querySelector("#validation-status");
    badge.classList.remove(
      "status-badge--success",
      "status-badge--warning",
      "status-badge--error",
    );
    if (snapshot.errors?.length || snapshot.status === "validation_failed") {
      badge.classList.add("status-badge--error");
      badge.lastChild.textContent = " Erros encontrados";
    } else if (reconciliationCount > 0) {
      badge.classList.add("status-badge--warning");
      badge.lastChild.textContent = " Revisão necessária";
    } else {
      badge.classList.add("status-badge--success");
      badge.lastChild.textContent = " Validado";
    }
  }

  function renderValidation(snapshot) {
    state.validation = snapshot;
    state.jobId = snapshot.job_id;
    const summary = snapshot.summary || {};
    renderGhes(summary);
    renderWarnings(snapshot.warnings || [], snapshot.errors || []);
    const reconciliationCount = renderReconciliation(
      snapshot.reconciliation,
      summary,
    );
    updateValidationStatus(snapshot, reconciliationCount);
    document.querySelector("#validated-file-count").textContent = String(
      state.uploadCount,
    );
    generateButton.disabled =
      Boolean(snapshot.errors?.length) ||
      snapshot.status === "validation_failed" ||
      snapshot.status === "failed";

    const reportUrl = snapshot.downloads?.validation_report;
    state.downloads.validationReport = Boolean(reportUrl);
    if (reportUrl) {
      earlyReportLink.href = "#";
      earlyReportLink.hidden = false;
    } else {
      earlyReportLink.hidden = true;
    }
    updateDownloadControls();
    document.querySelector("[data-step-link='3']").disabled = false;
    setStep(3);
  }

  function collectReconciliation() {
    const decisions = [];
    let valid = true;
    reconciliationList.querySelectorAll("select[data-source-id]").forEach((select) => {
      if (!select.value) {
        select.setAttribute("aria-invalid", "true");
        valid = false;
        return;
      }
      const notApplicable = select.value === "__not_applicable__";
      decisions.push({
        source_id: select.dataset.sourceId,
        action: notApplicable ? "not_applicable" : "map",
        official_ghe_code: notApplicable ? null : select.value,
      });
    });
    if (!valid) {
      throw new ApiError(
        "Defina uma decisão para todos os GHEs do Ergo.",
        { reconciliation: "há correspondências pendentes" },
      );
    }
    return decisions;
  }

  async function pollJob() {
    if (!state.jobId) return;
    if (Date.now() - state.pollStartedAt > 12 * 60 * 1000) {
      showError(
        "O processamento está demorando mais que o esperado. Consulte novamente o status da execução.",
      );
      return;
    }

    try {
      const response = await apiFetch(`/api/jobs/${encodeURIComponent(state.jobId)}`, {
        headers: { Accept: "application/json" },
      });
      const snapshot = await parseResponse(response);
      updateProgress(snapshot);
      if (snapshot.status === "completed") {
        state.pollTimer = null;
        showCompleted(snapshot);
        return;
      }
      if (
        snapshot.status === "failed" ||
        snapshot.status === "validation_failed"
      ) {
        state.pollTimer = null;
        setStep(3);
        showError(snapshot.error || "Não foi possível gerar o documento.");
        return;
      }
      state.pollTimer = window.setTimeout(pollJob, 750);
    } catch (error) {
      state.pollTimer = window.setTimeout(pollJob, 1600);
      if (error instanceof ApiError && error.status === 404) {
        window.clearTimeout(state.pollTimer);
        state.pollTimer = null;
        setStep(3);
        showError("A execução expirou. Valide os arquivos novamente.");
      }
    }
  }

  function showCompleted(snapshot) {
    const downloads = snapshot.downloads || {};
    state.downloads.document = Boolean(downloads.document);
    state.downloads.validationReport = Boolean(downloads.validation_report);
    documentLink.href = "#";
    reportLink.href = "#";
    updateDownloadControls();
    cleanupStatus.textContent = "";
    document.querySelector("[data-step-link='4']").disabled = false;
    setStep(4);
  }

  async function receiveArtifact(path, filename, kind) {
    const response = await apiFetch(path, {
      headers: { Accept: "application/octet-stream" },
    });
    if (!response.ok) {
      await parseResponse(response);
    }
    const blob = await response.blob();
    await validateArtifact(response, blob, kind);

    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.hidden = true;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
  }

  async function downloadArtifact(kind) {
    const jobId = state.jobId;
    const isDocument = kind === "document";
    const available = isDocument
      ? state.downloads.document
      : state.downloads.validationReport;
    const link = isDocument ? documentLink : reportLink;
    if (!jobId || !available || link.getAttribute("aria-disabled") === "true") {
      return;
    }
    if (state.downloadInProgress) {
      return;
    }

    state.downloadInProgress = true;
    updateDownloadControls();
    clearErrors();
    link.classList.add("is-downloading");
    link.setAttribute("aria-busy", "true");
    cleanupStatus.textContent = isDocument
      ? "Recebendo o documento completo antes da limpeza…"
      : "Recebendo o relatório de validação…";

    try {
      const suffix = isDocument ? "document" : "validation-report";
      const filename = isDocument
        ? "Documento AEP - AUTOMATICO.docx"
        : "Relatorio de Validacao AEP.json";
      await receiveArtifact(
        `/api/jobs/${encodeURIComponent(jobId)}/${suffix}`,
        filename,
        kind,
      );

      if (!isDocument) {
        cleanupStatus.textContent =
          "Relatório recebido. O documento Word continua disponível.";
        return;
      }

      const removed = await deleteJob(jobId);
      state.downloads.document = false;
      if (removed) {
        state.jobId = null;
        state.downloads.validationReport = false;
        earlyReportLink.hidden = true;
        cleanupStatus.textContent =
          "Documento recebido e arquivos da execução excluídos.";
      } else {
        cleanupStatus.textContent =
          "Documento recebido. A limpeza automática por prazo permanece agendada.";
      }
    } catch (error) {
      cleanupStatus.textContent = "";
      if (error instanceof ApiError) {
        showError(error.message, error.fields);
      } else {
        showError("Não foi possível baixar o arquivo. Tente novamente.");
      }
    } finally {
      state.downloadInProgress = false;
      updateDownloadControls();
      link.classList.remove("is-downloading");
      link.removeAttribute("aria-busy");
    }
  }

  form.querySelectorAll("[name='analysis_mode']").forEach((radio) => {
    radio.addEventListener("change", updateAnalysisMode);
  });

  document.querySelectorAll("[data-upload-card]").forEach((card) => {
    const input = card.querySelector("input[type='file']");
    const removeButton = card.querySelector("[data-remove-file]");

    input.addEventListener("change", () => {
      invalidateValidation();
      const file = input.files?.[0];
      if (file && !extensionAccepted(input, file)) {
        input.value = "";
        updateUploadCard(input);
        showError("O formato escolhido não é aceito.", {
          [input.name]: "use um dos formatos indicados no cartão",
        });
        return;
      }
      updateUploadCard(input);
      clearErrors();
    });

    removeButton.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      invalidateValidation();
      input.value = "";
      updateUploadCard(input);
    });

    ["dragenter", "dragover"].forEach((eventName) => {
      card.addEventListener(eventName, (event) => {
        event.preventDefault();
        card.classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach((eventName) => {
      card.addEventListener(eventName, (event) => {
        event.preventDefault();
        card.classList.remove("is-dragging");
      });
    });
    card.addEventListener("drop", (event) => {
      invalidateValidation();
      const file = event.dataTransfer?.files?.[0];
      if (!file) return;
      if (!extensionAccepted(input, file)) {
        showError("O formato arrastado não é aceito.", {
          [input.name]: "use um dos formatos indicados no cartão",
        });
        return;
      }
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      updateUploadCard(input);
      clearErrors();
    });
  });

  form.addEventListener("input", (event) => {
    if (event.target.matches("input:not([type='file']), textarea, select")) {
      invalidateValidation();
    }
  });

  form.addEventListener("change", (event) => {
    if (event.target.matches("input, textarea, select")) {
      invalidateValidation();
    }
  });

  document.querySelectorAll("[data-next-step]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextStep = Number(button.dataset.nextStep);
      if (state.step === 1 && !validateIdentification()) return;
      setStep(nextStep);
    });
  });

  document.querySelectorAll("[data-previous-step]").forEach((button) => {
    button.addEventListener("click", () => {
      setStep(Number(button.dataset.previousStep));
    });
  });

  document.querySelectorAll("[data-step-link]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.disabled) return;
      const target = Number(button.dataset.stepLink);
      if (target === 1 || target === 2) {
        setStep(target);
      } else if (target === 3 && state.validation) {
        setStep(3);
      } else if (target === 4 && state.validation?.status === "completed") {
        setStep(4);
      }
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!validateIdentification() || !validateUploads()) return;
    clearErrors();
    setButtonBusy(validateButton, true, "Validando arquivos…");

    try {
      const payload = new FormData(form);
      payload.set("compatibility_mode", String(compatibilityMode.checked));
      state.uploadCount = [...form.querySelectorAll("input[type='file']")].filter(
        (input) => input.files?.length,
      ).length;
      const response = await apiFetch("/api/validate", {
        method: "POST",
        body: payload,
        headers: { Accept: "application/json" },
      });
      const snapshot = await parseResponse(response);
      renderValidation(snapshot);
    } catch (error) {
      if (error instanceof ApiError) {
        showError(error.message, error.fields);
      } else {
        showError(
          "Não foi possível conectar ao serviço de processamento. Tente novamente em instantes.",
        );
      }
    } finally {
      setButtonBusy(validateButton, false, "");
    }
  });

  generateButton.addEventListener("click", async () => {
    clearErrors();
    try {
      const reconciliations = collectReconciliation();
      setButtonBusy(generateButton, true, "Iniciando geração…");
      const response = await apiFetch("/api/generate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({
          job_id: state.jobId,
          reconciliations,
          compatibility_mode: compatibilityMode.checked,
        }),
      });
      const snapshot = await parseResponse(response);
      if (snapshot.status === "completed") {
        showCompleted(snapshot);
        return;
      }
      showProcessing(snapshot);
      state.pollStartedAt = Date.now();
      state.pollTimer = window.setTimeout(pollJob, 500);
    } catch (error) {
      if (error instanceof ApiError) {
        showError(error.message, error.fields);
      } else {
        showError("Não foi possível iniciar a geração do documento.");
      }
    } finally {
      setButtonBusy(generateButton, false, "");
    }
  });

  document.querySelector("#back-to-files").addEventListener("click", () => {
    setStep(2);
  });

  document.querySelector("#new-document-button").addEventListener("click", () => {
    const completedJobId = state.jobId;
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
    }
    state.jobId = null;
    state.validation = null;
    state.pollTimer = null;
    state.pollStartedAt = null;
    state.uploadCount = 0;
    state.downloads.document = false;
    state.downloads.validationReport = false;
    form.reset();
    document.querySelectorAll("input[type='file']").forEach(updateUploadCard);
    reconciliationList.replaceChildren();
    earlyReportLink.hidden = true;
    updateAnalysisMode();
    clearErrors();
    cleanupStatus.textContent = "";
    setStep(1);
    if (completedJobId) {
      void deleteJob(completedJobId);
    }
  });

  document.querySelector("#dismiss-error").addEventListener("click", clearErrors);
  errorPanel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") clearErrors();
  });

  for (const link of [documentLink, reportLink]) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      if (link.getAttribute("aria-disabled") === "true") return;
      void downloadArtifact(link === documentLink ? "document" : "report");
    });
  }
  earlyReportLink.addEventListener("click", (event) => {
    event.preventDefault();
    if (
      earlyReportLink.hidden ||
      earlyReportLink.getAttribute("aria-disabled") === "true"
    ) {
      return;
    }
    void downloadArtifact("report");
  });

  updateAnalysisMode();
  updateDownloadControls();
  setStep(1);
  if (!apiBaseUrl) {
    validateButton.disabled = true;
    showError(
      "O endereço do serviço de processamento ainda não foi configurado para esta página.",
    );
  }
})();
