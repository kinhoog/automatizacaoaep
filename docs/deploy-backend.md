# Implantação do backend no Render

## Visão geral

O backend permanece em Python/FastAPI e é executado em um Web Service Docker. O Render termina a conexão HTTPS, encaminha a requisição ao container e fornece a variável `PORT`. A aplicação escuta em `0.0.0.0`, mantém os jobs em `/tmp/aep-jobs` e não solicita disco persistente.

Referências oficiais:

- [Web Services no Render](https://render.com/docs/web-services);
- [Implantações com Docker](https://render.com/docs/docker);
- [Blueprints com `render.yaml`](https://render.com/docs/infrastructure-as-code);
- [Environment Variables e Secret Files](https://render.com/docs/configure-environment-variables);
- [Discos e sistema de arquivos efêmero](https://render.com/docs/disks).

## Artefatos versionados

- `Dockerfile`: Python, dependências, LibreOffice headless, fontes, usuário não root, health check e Uvicorn;
- `.dockerignore`: exclui áreas privadas, temporários, testes e formatos documentais;
- `render.yaml`: Web Service Docker, uma instância, health check e variáveis não secretas;
- `.github/workflows/ci.yml`: testes Python, build e inspeção do container;
- `scripts/prepare_hosted_template_secret.py`: valida e converte o template privado em arquivos Base64.

Nenhum template é copiado para a imagem.

## Preparar os arquivos secretos

Execute esta etapa em uma estação autorizada, dentro do ambiente virtual do projeto:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_hosted_template_secret.py `
  private_templates\aep_template.docx `
  --manifest private_templates\aep_template.manifest.json `
  --compatibility-profile private_templates\aep_compatibility_profile.json
```

Antes de codificar, o script:

- valida o tipo real do DOCX;
- confere o hash, o manifesto de slots e o saneamento;
- valida o perfil de compatibilidade, quando informado;
- calcula o tamanho Base64;
- recusa diretório de saída fora de `private_templates/`;
- recusa um conjunto maior que o limite configurado.

Os resultados ficam em `private_templates/hosted_secret/`:

| Arquivo local privado | Secret File no Render | Variável de caminho |
| --- | --- | --- |
| `aep_template.docx.b64` | `aep_template.docx.b64` | `AEP_HOSTED_TEMPLATE_BASE64_FILE=/etc/secrets/aep_template.docx.b64` |
| `aep_template.manifest.json.b64` | `aep_template.manifest.json.b64` | `AEP_HOSTED_TEMPLATE_MANIFEST_BASE64_FILE=/etc/secrets/aep_template.manifest.json.b64` |
| `aep_compatibility_profile.json.b64` | `aep_compatibility_profile.json.b64` | `AEP_HOSTED_COMPATIBILITY_PROFILE_BASE64_FILE=/etc/secrets/aep_compatibility_profile.json.b64` |

Não envie o arquivo de metadados gerado como secret e não copie o conteúdo Base64 para o Git, Dockerfile, Pages, release ou log.

O conjunto privado medido atualmente ocupa **918.504 bytes em Base64**, abaixo do limite conjunto de **1 MiB** aplicado pelo script. Se uma futura versão ultrapassar esse limite:

1. não fragmente nem publique o conteúdo em arquivos rastreados;
2. escolha um mecanismo privado de arquivo secreto com limite suficiente;
3. monte o arquivo somente no runtime;
4. mantenha a validação de hash e manifesto antes de habilitar a pipeline;
5. registre a decisão operacional sem incluir o material privado.

## Criar o Blueprint

1. no painel do Render, selecione **New → Blueprint**;
2. conecte o repositório público;
3. selecione a branch `main`;
4. permita que o Render leia `render.yaml`;
5. revise o serviço `automatizador-aep-api`;
6. confirme o plano gratuito definido por `plan: free`; não cadastre cartão para o piloto;
7. não adicione banco de dados;
8. não adicione Persistent Disk;
9. mantenha uma única instância, pois os jobs e metadados ficam em memória e não há armazenamento compartilhado;
10. crie o serviço.

Depois da criação, abra o serviço e cadastre os três **Secret Files**, usando exatamente os nomes da tabela. Cole em cada um apenas o conteúdo Base64 do arquivo correspondente.

O `render.yaml` já define:

```text
AEP_ALLOWED_ORIGINS=https://kinhoog.github.io
AEP_REQUIRE_ORIGIN=true
AEP_RUNTIME_DIR=/tmp/aep-jobs
AEP_JOB_TTL_SECONDS=900
AEP_TEMPLATE_PATH=/tmp/aep-runtime/template/aep_template.docx
AEP_TEMPLATE_MANIFEST_PATH=/tmp/aep-runtime/template/aep_template.manifest.json
AEP_ALLOW_SYNTHETIC_TEMPLATE_FALLBACK=false
```

O provedor fornece `PORT`; o valor do Blueprint serve como configuração inicial. Não grave uma URL interna, token ou credencial nessas variáveis.

## Inicialização e validação do template

No início do processo:

1. a aplicação lê os três arquivos Base64 pelos caminhos configurados;
2. decodifica o material em um subdiretório privado e aleatório sob `/tmp/aep-jobs/`;
3. verifica hash, manifesto, estrutura e saneamento;
4. disponibiliza o perfil de compatibilidade no diretório privado do runtime;
5. mantém a pipeline fechada se qualquer validação obrigatória falhar.

O endpoint de health deve responder `200` somente quando a aplicação e a pipeline estiverem prontas:

```powershell
Invoke-RestMethod "https://URL-DO-BACKEND/api/health"
```

Resultado esperado:

```json
{
  "status": "ok",
  "pipeline_ready": true
}
```

Uma resposta degradada ou `503` exige inspeção das variáveis e Secret Files. Não habilite fallback sintético em produção.

## CORS e origem

Em produção:

```text
AEP_ALLOWED_ORIGINS=https://kinhoog.github.io
AEP_REQUIRE_ORIGIN=true
```

A origem não inclui `/automatizacaoaep/`, pois o cabeçalho HTTP `Origin` contém somente esquema, host e porta. Não use `*`.

Os métodos permitidos são `GET`, `POST`, `DELETE` e `OPTIONS`, e os cabeçalhos aceitos ficam restritos ao necessário para uploads e respostas. O endpoint de saúde pode ser consultado pelo provedor sem `Origin`; as rotas operacionais exigem uma origem permitida.

CORS é uma proteção de navegador, não autenticação. Um cliente fora do navegador pode construir requisições diretamente; por isso a URL não deve ser descrita como privada ou autenticada.

## Ciclo do job

Cada execução recebe um identificador imprevisível e um diretório:

```text
/tmp/aep-jobs/<job_id>/
```

Fluxo preferencial:

1. `POST /api/validate`;
2. revisão e `POST /api/generate`;
3. polling em `GET /api/jobs/{id}`;
4. relatório opcional em `GET /api/jobs/{id}/validation-report`;
5. DOCX em `GET /api/jobs/{id}/document`;
6. o navegador termina de receber o `Blob`;
7. `DELETE /api/jobs/{id}` remove o job.

Como alternativa, `GET /api/jobs/{id}/download` programa a limpeza para depois do término da resposta. Nenhuma rota deve apagar o arquivo antes de o corpo do download ser enviado.

Jobs abandonados expiram depois de 900 segundos. A inicialização e uma rotina periódica tentam remover diretórios órfãos vencidos. A reinicialização do serviço também descarta o estado em memória e o filesystem efêmero do provedor não deve ser tratado como armazenamento durável.

## Validar a implantação

1. confirme o health check;
2. execute um fluxo completo com fixtures sintéticas;
3. confira `Cache-Control: no-store` nas respostas da API;
4. envie uma requisição com `Origin: https://kinhoog.github.io` e confirme o cabeçalho CORS;
5. envie uma origem não autorizada e confirme a rejeição;
6. baixe o DOCX por completo antes de excluir;
7. confirme que o `DELETE` torna o job indisponível;
8. crie um job sintético, abandone-o e confirme a remoção após o TTL;
9. confirme que reiniciar o container não recupera jobs antigos;
10. inspecione a imagem e confirme que não contém `private_templates/`, `local_samples/`, uploads, saídas ou documentos reais.

Depois, configure `AEP_API_BASE_URL` no GitHub com a origem HTTPS do serviço e republique o Pages.

## CI e atualização

O workflow de CI:

- instala as dependências;
- executa a suíte Python;
- constrói a imagem;
- procura documentos e áreas privadas dentro da imagem;
- inicia o container com template sintético de teste;
- confere usuário não root, grupo de leitura de secrets, diretório temporário, CORS e Docker health check.

Para atualizar:

1. rode os testes locais e a regressão privada;
2. revise privacidade, diff e arquivos preparados;
3. envie a alteração para `main`;
4. aguarde o CI;
5. aguarde o auto-deploy do Render;
6. confira `/api/health`;
7. valide o frontend público;
8. se o template mudou, prepare e substitua os Secret Files antes de liberar a pipeline.

## Limitações operacionais

- não há login nem autenticação;
- não há fila distribuída;
- no plano gratuito, o serviço hiberna após um período sem tráfego e a primeira chamada pode levar cerca de um minuto;
- o estado dos jobs fica em memória;
- o serviço deve permanecer com uma instância;
- reinicializações e deploys podem interromper trabalhos ativos;
- a remoção é acionada pela aplicação e pelo TTL, mas não representa garantia de apagamento forense da infraestrutura do provedor;
- a disponibilidade e eventuais políticas de logs da plataforma também dependem da conta e do plano contratados.
