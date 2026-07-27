# Automatizador de Documentos AEP

Aplicação web local para validar fontes técnicas já aprovadas, reconciliar GHEs e compilar um documento AEP editável em `.docx`. Todo o processamento ocorre no computador onde o servidor é executado: o sistema não envia documentos a serviços externos e não elabora novas conclusões técnicas.

## O que o MVP faz

- recebe a planilha oficial de GHEs, relatórios Word, cartão cadastral e logo opcional;
- aceita análise técnica integrada ou dois relatórios técnicos separados;
- detecta o tipo real dos arquivos e rejeita extensões ou conteúdos incompatíveis;
- extrai GHEs, população, blocos ergonômicos, imagens psicossociais e textos técnicos;
- mostra divergências e exige uma reconciliação explícita dos GHEs;
- monta o Word usando um template privado saneado e parametrizado;
- produz um relatório JSON de validação para auditoria;
- mantém o DOCX final editável e solicita ao Word a atualização dos campos ao abrir.

O compilador somente organiza conteúdo existente. A aprovação técnica, a validade legal e a revisão final permanecem sob responsabilidade dos profissionais competentes.

## Requisitos

- Windows 10 ou 11;
- Python 3.11 ou superior;
- PowerShell 5.1 ou superior;
- LibreOffice Writer recomendado para conversão, renderização e auditoria visual;
- Microsoft Word é um fallback local de renderização no Windows, quando disponível.

O LibreOffice é necessário para converter um `.doc` binário verdadeiro. Arquivos `.doc` cujo conteúdo real seja HTML compatível com Word são lidos diretamente.

## Instalação no PowerShell

Abra o PowerShell no diretório do projeto:

```powershell
Set-Location "C:\caminho\para\Automatizador de Documentos AEP"
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Se a política de execução impedir scripts locais, libere-os somente para a sessão atual:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Verificar o LibreOffice

Na instalação padrão de 64 bits:

```powershell
$soffice = "C:\Program Files\LibreOffice\program\soffice.exe"
Test-Path -LiteralPath $soffice
& $soffice --headless --version
```

Se estiver em outro local, preencha no arquivo `.env`:

```dotenv
AEP_LIBREOFFICE_PATH=C:\caminho\para\LibreOffice\program\soffice.exe
```

`AEP_RENDER_ON_GENERATE=true` habilita a renderização de conferência depois da montagem. Deixe `false` se quiser gerar apenas o DOCX.

## Preparar o template privado

O repositório público não contém o modelo oficial. Guarde a cópia aprovada fora do histórico do Git e gere o template parametrizado localmente:

```powershell
New-Item -ItemType Directory -Force private_templates | Out-Null
.\.venv\Scripts\python.exe scripts\prepare_private_template.py `
  "C:\caminho\privado\Modelo AEP aprovado.docx" `
  "private_templates\aep_template.docx"
```

O comando cria `private_templates\aep_template.docx` e `private_templates\aep_template.manifest.json`. Na cópia, textos e imagens dependentes da empresa são substituídos por marcadores ou conteúdo neutro; o manifesto registra hashes, estrutura, slots e capacidade do layout. O original não é alterado e a pasta inteira é ignorada pelo Git.

A aplicação opera em modo fechado: `/api/health` só informa que a pipeline está pronta quando template e manifesto existem, correspondem entre si e passam pela auditoria de saneamento; enquanto isso, `/api/validate` responde `503`. Se a estrutura do modelo mudar, gere novamente o template e execute a regressão estrutural e visual.

### Preparar um perfil privado de compatibilidade

Use esta etapa somente quando uma regressão privada precisar reproduzir uma exceção já aprovada do gabarito. O perfil fica vinculado pelos hashes ao conjunto exato de entradas e não funciona com outros arquivos:

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

O resultado padrão é `private_templates\aep_compatibility_profile.json`, também ignorado. Sem um perfil válido e compatível, a opção é rejeitada explicitamente. Alterar os arquivos ou o modo depois da validação invalida o job e exige uma nova validação.

## Iniciar

O script cria o ambiente virtual se necessário, instala as dependências, lê o `.env`, verifica o LibreOffice e inicia o servidor:

```powershell
.\iniciar.ps1
```

Por padrão, o sistema abre em [http://127.0.0.1:8000](http://127.0.0.1:8000). Para não abrir o navegador ou para pular uma reinstalação já realizada:

```powershell
.\iniciar.ps1 -SemAbrirNavegador -SemInstalarDependencias
```

Execução manual equivalente:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Usar a interface

1. Informe razão social, competência e datas-base.
2. Envie a planilha de GHEs, o relatório psicossocial bruto, o relatório Ergo e o cartão cadastral.
3. Selecione o modo de análise:
   - **Integrado:** um relatório técnico contém as duas análises;
   - **Separado:** envie uma análise psicossocial e uma análise ergonômica.
4. Envie a logo, se houver. Sua ausência não cria espaço ou marcador no documento.
5. Clique em **Validar arquivos**.
6. Confira GHEs, população, avisos e cada correspondência sugerida.
7. Para cada bloco divergente, escolha um GHE oficial ou marque-o como não aplicável.
8. Revise as decisões e clique em **Gerar documento AEP**.
9. Acompanhe o processamento e baixe o DOCX e o relatório de validação.

Nenhum GHE é renumerado, removido ou corrigido silenciosamente. O modo de compatibilidade é uma exceção auditável para regressões privadas; ele não deve ser usado como regra geral.

## Formatos de entrada

| Entrada | Formatos | Obrigatória |
| --- | --- | --- |
| Planilha oficial de GHEs | `.xlsx` | Sim |
| Relatório psicossocial bruto | `.docx` | Sim |
| Relatório Ergo bruto | `.doc`, `.docx` | Sim |
| Relatório técnico integrado | `.docx` | No modo integrado |
| Análises técnicas separadas | `.docx` + `.docx` | No modo separado |
| Cartão cadastral | `.png`, `.jpg`, `.jpeg`, `.webp` | Sim |
| Logo da empresa | `.png`, `.jpg`, `.jpeg`, `.webp` | Não |

Os limites padrão são 25 MB por arquivo e 250 MB por requisição; podem ser ajustados no `.env`.

## Arquivos e retenção

- `uploads/`: área temporária, isolada por identificador aleatório de execução;
- `generated/`: artefatos intermediários locais;
- `outputs/`: saídas locais de regressão ou operação;
- `private_templates/`: template e manifesto privados;
- `local_samples/`: amostras confidenciais usadas somente fora dos testes públicos;
- `tests/fixtures/public_synthetic/`: únicos documentos e imagens permitidos no Git.

As cinco primeiras áreas de dados são ignoradas pelo Git. Jobs temporários expiram por padrão em 60 minutos e são removidos pelo processo de limpeza. Uma varredura de inicialização e outra periódica tentam remover diretórios órfãos expirados; falhas transitórias ficam disponíveis para uma nova tentativa. Artefatos de regressão criados manualmente em `outputs/` não são apagados pelo TTL. O download imediato continua sendo a forma recomendada de guardar o resultado.

## Testes

Os testes versionados usam somente dados sintéticos, marcados como sem validade:

```powershell
.\.venv\Scripts\python.exe scripts\create_synthetic_fixtures.py
.\.venv\Scripts\python.exe -m pytest
```

Execução por camada:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit
.\.venv\Scripts\python.exe -m pytest tests\integration
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing
```

Nunca aponte um teste público para `local_samples/` nem copie conteúdo real para fixtures.

## API local

| Método | Rota | Uso |
| --- | --- | --- |
| `GET` | `/` | Interface web |
| `GET` | `/api/health` | Saúde do serviço e disponibilidade da pipeline |
| `POST` | `/api/validate` | Upload e validação |
| `POST` | `/api/generate` | Reconciliação e início da geração |
| `GET` | `/api/jobs/{id}` | Estado e progresso |
| `GET` | `/api/jobs/{id}/document` | Download do DOCX |
| `GET` | `/api/jobs/{id}/validation-report` | Download do JSON de validação |

Os identificadores são aleatórios. A API não aceita caminhos de arquivo fornecidos pelo cliente.

## Configuração

O `iniciar.ps1` carrega o arquivo `.env`; variáveis já definidas no processo têm precedência.

| Variável | Padrão | Finalidade |
| --- | --- | --- |
| `AEP_HOST` | `127.0.0.1` | Endereço de escuta |
| `AEP_PORT` | `8000` | Porta HTTP |
| `AEP_MAX_FILE_MB` | `25` | Limite individual |
| `AEP_MAX_REQUEST_MB` | `250` | Limite da requisição |
| `AEP_RUNTIME_DIR` | `uploads` | Diretório temporário |
| `AEP_JOB_TTL_SECONDS` | `3600` | Retenção dos jobs da API |
| `AEP_JOB_TTL_MINUTES` | `60` | Retenção dos serviços internos |
| `AEP_TEMPLATE_PATH` | `private_templates/aep_template.docx` | Template privado |
| `AEP_TEMPLATE_MANIFEST_PATH` | `private_templates/aep_template.manifest.json` | Manifesto de slots |
| `AEP_ALLOW_SYNTHETIC_TEMPLATE_FALLBACK` | `false` | Fallback permitido somente nos testes sintéticos |
| `AEP_COMPATIBILITY_PROFILE_PATH` | `private_templates/aep_compatibility_profile.json` | Perfil privado vinculado às entradas |
| `AEP_RENDER_ON_GENERATE` | `false` | Renderização após gerar |
| `AEP_LIBREOFFICE_PATH` | vazio | Caminho opcional do `soffice.exe` |

Mantenha `AEP_ALLOW_SYNTHETIC_TEMPLATE_FALLBACK=false` em uso real. Não use `0.0.0.0` em uma estação com rede não confiável: o MVP não possui autenticação.

## Privacidade e segurança

- arquivos são processados localmente e nunca enviados a APIs externas;
- extensão, assinatura real, tamanho e estrutura OOXML interna são validados;
- pacotes com macros, travessia de diretório, relações externas perigosas ou expansão ZIP excessiva são rejeitados;
- nomes recebidos são sanitizados e o cliente não controla caminhos;
- cada execução usa um diretório exclusivo;
- documentos `.doc` OLE são convertidos pelo LibreOffice headless em perfil isolado, com execução de macros desabilitada;
- o template é conferido por hash, manifesto, marcadores, mídia neutra e capacidade antes da montagem;
- logs não devem registrar conteúdo, razão social, nomes individuais ou caminhos privados;
- o modelo normalizado não guarda nomes de colaboradores;
- `.gitignore` bloqueia documentos, planilhas, imagens, uploads, templates e saídas reais.

Antes de qualquer commit:

```powershell
git status --short
git diff --cached --name-only
git diff --cached
```

Consulte [docs/privacidade.md](docs/privacidade.md) para a lista operacional completa.

## Limitações atuais

- o MVP é local, sem login, banco de dados, armazenamento permanente ou execução distribuída;
- o estado dos jobs fica em memória; reiniciar o servidor encerra o acompanhamento em curso;
- mudanças estruturais no modelo Word exigem novo manifesto e validação visual;
- o template privado atual declara capacidade para três GHEs; uma entrada maior é bloqueada explicitamente, sem truncamento, até que exista um template com mais slots;
- o PDF é secundário; o artefato oficial do fluxo é o DOCX editável;
- renderização visual depende de LibreOffice, ou do Word local como fallback no Windows;
- documentos antigos `.doc` binários dependem de uma conversão segura pelo LibreOffice;
- a qualidade final depende da integridade e da aprovação prévia dos relatórios de origem.

Detalhes de componentes e fluxo estão em [docs/arquitetura.md](docs/arquitetura.md).
