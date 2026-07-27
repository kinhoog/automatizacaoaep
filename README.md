# Automatizador de Documentos AEP

Aplicação web para validar fontes técnicas já aprovadas, reconciliar GHEs e compilar um documento AEP editável em `.docx`. A entrega pública usa um frontend estático no GitHub Pages e um backend Python/FastAPI em container. O compilador organiza o conteúdo recebido; ele não cria novas conclusões técnicas.

## Uso público

A interface pública fica em:

[https://kinhoog.github.io/automatizacaoaep/](https://kinhoog.github.io/automatizacaoaep/)

Quando o backend estiver configurado e disponível, o uso não exige instalação:

1. acesse a URL;
2. preencha os dados da empresa e do documento;
3. envie a planilha oficial de GHEs, os relatórios e o cartão cadastral;
4. escolha entre relatório técnico integrado ou duas análises separadas;
5. envie uma logo somente se houver;
6. clique em **Validar arquivos**;
7. confira os GHEs, a população, os avisos e a reconciliação sugerida;
8. relacione cada bloco divergente a um GHE oficial ou marque-o como não aplicável;
9. gere o documento;
10. baixe primeiro o relatório de validação, se desejado, e depois o DOCX.

O frontend recebe o Word por completo como `Blob`, inicia o download e então solicita a exclusão explícita do job. Se essa solicitação não chegar ao backend, uma limpeza por expiração atua como salvaguarda.

A página apresenta o seguinte aviso:

> Os arquivos são utilizados somente durante a geração do documento. Não há banco de dados ou armazenamento permanente. Após o download, os arquivos da execução são excluídos automaticamente.

Essa política descreve o ciclo de vida implementado pela aplicação. O processamento ocorre na infraestrutura temporária do provedor hospedado e a remoção depende da conclusão do download, da solicitação de exclusão ou da rotina de expiração. Consulte [docs/retencao-e-privacidade.md](docs/retencao-e-privacidade.md).

Formatos aceitos:

| Entrada | Formatos | Obrigatória |
| --- | --- | --- |
| Planilha oficial de GHEs | `.xlsx` | Sim |
| Relatório psicossocial bruto | `.docx` | Sim |
| Relatório Ergo bruto | `.doc`, `.docx` | Sim |
| Relatório técnico integrado | `.docx` | No modo integrado |
| Análises técnicas separadas | `.docx` + `.docx` | No modo separado |
| Cartão cadastral | `.png`, `.jpg`, `.jpeg`, `.webp` | Sim |
| Logo da empresa | `.png`, `.jpg`, `.jpeg`, `.webp` | Não |

Nenhum GHE é renumerado, removido ou corrigido silenciosamente. A planilha é a fonte oficial para código, nome, setores, cargos e população. A aprovação técnica, a validade legal e a revisão final permanecem sob responsabilidade dos profissionais competentes.

## Desenvolvimento local

### Requisitos

- Windows 10 ou 11;
- Python 3.11 ou superior;
- PowerShell 5.1 ou superior;
- LibreOffice Writer para conversão de `.doc` binário e auditoria visual.

Abra o PowerShell no diretório do projeto:

```powershell
Set-Location "C:\caminho\para\Automatizador de Documentos AEP"
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Se a política de execução bloquear scripts locais, autorize-os somente na sessão atual:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Verifique o LibreOffice:

```powershell
$soffice = "C:\Program Files\LibreOffice\program\soffice.exe"
Test-Path -LiteralPath $soffice
& $soffice --headless --version
```

Se necessário, informe outro caminho em `.env`:

```dotenv
AEP_LIBREOFFICE_PATH=C:\caminho\para\LibreOffice\program\soffice.exe
```

### Template privado local

O repositório público não contém o modelo oficial. Guarde a cópia aprovada fora do histórico e gere o template saneado:

```powershell
New-Item -ItemType Directory -Force private_templates | Out-Null
.\.venv\Scripts\python.exe scripts\prepare_private_template.py `
  "C:\caminho\privado\Modelo AEP aprovado.docx" `
  "private_templates\aep_template.docx"
```

O comando cria `private_templates/aep_template.docx` e `private_templates/aep_template.manifest.json`. O original não é alterado e toda a pasta permanece ignorada pelo Git.

Um perfil privado de compatibilidade pode ser criado somente para reproduzir uma exceção já aprovada e vinculada às fontes exatas:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_compatibility_profile.py `
  --ghe "C:\fontes-privadas\ghes.xlsx" `
  --psychosocial "C:\fontes-privadas\psicossocial.docx" `
  --ergo "C:\fontes-privadas\ergo.doc" `
  --registration-card "C:\fontes-privadas\cartao.png" `
  --integrated "C:\fontes-privadas\tecnico-integrado.docx" `
  --include-ergo 1 2 3 `
  --omit-ergo 4
```

### Iniciar e testar

Inicialização simplificada:

```powershell
.\iniciar.ps1
```

Execução manual da API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app `
  --host 127.0.0.1 `
  --port 8000
```

O endpoint local de saúde fica em `http://127.0.0.1:8000/api/health`. Para testar o frontend público separadamente, configure uma origem local explícita em `AEP_ALLOWED_ORIGINS`; não use `*`.

Testes:

```powershell
.\.venv\Scripts\python.exe scripts\create_synthetic_fixtures.py
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing
```

Os testes versionados usam somente `tests/fixtures/public_synthetic/`. Nunca aponte testes públicos para `local_samples/` nem copie conteúdo real para fixtures.

Principais variáveis locais:

| Variável | Padrão | Finalidade |
| --- | --- | --- |
| `AEP_HOST` | `127.0.0.1` no `.env.example` | Endereço de escuta do Uvicorn |
| `PORT` ou `AEP_PORT` | `8000` | Porta HTTP; `PORT` tem precedência |
| `AEP_MAX_FILE_MB` | `25` | Limite individual |
| `AEP_MAX_REQUEST_MB` | `250` | Limite total da requisição |
| `AEP_RUNTIME_DIR` | `uploads` no `.env.example` | Raiz transitória dos jobs |
| `AEP_JOB_TTL_SECONDS` | `900` | Expiração de jobs abandonados |
| `AEP_TEMPLATE_PATH` | `private_templates/aep_template.docx` | Template saneado |
| `AEP_TEMPLATE_MANIFEST_PATH` | `private_templates/aep_template.manifest.json` | Manifesto validado |
| `AEP_COMPATIBILITY_PROFILE_PATH` | arquivo privado | Perfil opcional |
| `AEP_ALLOWED_ORIGINS` | `https://kinhoog.github.io` | Origens permitidas, separadas por vírgula |
| `AEP_REQUIRE_ORIGIN` | `true` | Exige origem permitida nas rotas operacionais |
| `AEP_RENDER_ON_GENERATE` | `false` | Auditoria de renderização após gerar |

Mantenha `AEP_ALLOW_SYNTHETIC_TEMPLATE_FALLBACK=false` fora dos testes.

## Implantação

A arquitetura de produção é:

```text
GitHub Pages
    ↓ HTTPS
frontend/ estático
    ↓ HTTPS
FastAPI em container no Render
    ↓
/tmp/aep-jobs/<job_id>/
    ↓
download do DOCX
    ↓
DELETE explícito ou limpeza por TTL
```

### GitHub Pages

O workflow [`.github/workflows/deploy-pages.yml`](.github/workflows/deploy-pages.yml) publica somente `frontend/` em pushes para `main`. Durante o build ele gera `frontend/config.js` com a variável de repositório:

```text
AEP_API_BASE_URL=https://URL-HTTPS-DO-BACKEND
```

Cadastre essa variável em **Settings → Secrets and variables → Actions → Variables**. Não coloque tokens, credenciais ou arquivos privados no frontend. Se a variável estiver vazia, a página continua publicável, mas bloqueia uploads e explica que o serviço ainda não foi configurado.

O fluxo usa as ações oficiais `configure-pages`, `upload-pages-artifact` e `deploy-pages`. Veja [GitHub Pages com workflow personalizado](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) e o guia detalhado em [docs/deploy-github-pages.md](docs/deploy-github-pages.md).

### Backend no Render

O arquivo [`render.yaml`](render.yaml) descreve um único Web Service Docker. O container:

- escuta em `0.0.0.0` e usa `PORT`;
- inclui Python, LibreOffice headless e fontes compatíveis;
- executa com usuário não root;
- usa `/tmp/aep-jobs` e não solicita disco persistente;
- expõe `/api/health`;
- restringe CORS a `https://kinhoog.github.io`;
- define TTL inicial de 900 segundos.

Crie o serviço como um [Render Blueprint](https://render.com/docs/infrastructure-as-code) apontando para este repositório. O armazenamento local padrão do serviço é efêmero; não adicione Persistent Disk. Consulte também [Web Services](https://render.com/docs/web-services), [Docker no Render](https://render.com/docs/docker) e [sistema de arquivos efêmero](https://render.com/docs/disks).

O template não entra no Git nem na imagem. Prepare os três arquivos Base64 privados:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_hosted_template_secret.py `
  private_templates\aep_template.docx `
  --manifest private_templates\aep_template.manifest.json `
  --compatibility-profile private_templates\aep_compatibility_profile.json
```

O resultado fica em `private_templates/hosted_secret/`, também ignorado:

- `aep_template.docx.b64`;
- `aep_template.manifest.json.b64`;
- `aep_compatibility_profile.json.b64`.

No Render, cadastre-os como **Secret Files** com esses nomes. Eles ficam disponíveis, respectivamente, nos caminhos `/etc/secrets/aep_template.docx.b64`, `/etc/secrets/aep_template.manifest.json.b64` e `/etc/secrets/aep_compatibility_profile.json.b64`. O backend decodifica os arquivos em área temporária, confere hash e manifesto e mantém a pipeline indisponível se a validação falhar. Consulte [Environment Variables and Secrets](https://render.com/docs/configure-environment-variables).

A medição local do conjunto atual foi de **918.504 bytes em Base64**, abaixo do limite conjunto de **1 MiB** usado na preparação. O script recusa a geração se o limite configurado for excedido. Se uma versão futura ultrapassar esse limite, use um mecanismo privado do provedor com capacidade maior, sem publicar o template no repositório, no Pages, em release ou na imagem.

Depois que o Render fornecer a URL HTTPS:

1. confirme `GET https://URL-DO-BACKEND/api/health`, esperando `status: "ok"` e `pipeline_ready: true`;
2. grave essa URL em `AEP_API_BASE_URL`;
3. execute novamente o workflow do Pages;
4. valide o fluxo pela URL pública;
5. confirme o download, o `DELETE /api/jobs/{id}` e a expiração de um job abandonado;
6. confira a execução do CI, que testa a suíte Python, o build Docker, o usuário não root, a ausência de arquivos privados na imagem e o health check do container.

O backend mantém estado apenas em memória e usa uma única instância, sem banco de dados nem armazenamento compartilhado. Uma reinicialização pode interromper jobs ativos. CORS restringe o uso normal por navegadores, mas não substitui autenticação. Detalhes de configuração e validação estão em [docs/deploy-backend.md](docs/deploy-backend.md).

Rotas hospedadas:

| Método | Rota | Uso |
| --- | --- | --- |
| `GET` | `/api/health` | Saúde e prontidão da pipeline |
| `POST` | `/api/validate` | Upload e validação |
| `POST` | `/api/generate` | Reconciliação e geração |
| `GET` | `/api/jobs/{id}` | Estado e progresso |
| `GET` | `/api/jobs/{id}/document` | DOCX sem remoção antecipada |
| `GET` | `/api/jobs/{id}/validation-report` | Relatório JSON |
| `GET` | `/api/jobs/{id}/download` | DOCX com limpeza após a resposta |
| `DELETE` | `/api/jobs/{id}` | Exclusão explícita do job |

Detalhes dos componentes estão em [docs/arquitetura.md](docs/arquitetura.md).
