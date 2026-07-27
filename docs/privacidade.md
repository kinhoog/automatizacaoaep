# Privacidade e operação segura

## Princípio

Relatórios empresariais, planilhas, imagens cadastrais, templates oficiais e documentos gerados são confidenciais. Eles nunca entram no repositório público. Na versão hospedada, os arquivos enviados deixam o computador do usuário e são processados temporariamente pelo backend em container; a aplicação não os encaminha a APIs de análise ou a serviços permanentes de documentos.

Detalhes do ciclo hospedado estão em [retencao-e-privacidade.md](retencao-e-privacidade.md).

## Classificação dos arquivos

| Classe | Exemplos | Git |
| --- | --- | --- |
| Confidencial | relatórios, planilhas, imagens cadastrais, logos e amostras reais | Proibido |
| Template privado | gabarito, template saneado, manifesto, perfil e Base64 | Proibido |
| Artefato operacional | uploads, intermediários, DOCX/PDF/JSON e renders | Proibido |
| Público sintético | fixtures criadas do zero e marcadas como sem validade | Permitido somente na pasta autorizada |
| Código e documentação | fonte sem dados reais e instruções genéricas | Permitido após revisão |

Pastas protegidas:

- `local_samples/`;
- `private_templates/`;
- `uploads/`;
- `generated/`;
- `outputs/`.

O `.gitignore` também bloqueia globalmente extensões documentais e imagens. Apenas `tests/fixtures/public_synthetic/` possui exceções explícitas.

## Regras para dados

- não alterar ou sobrescrever a fonte original;
- trabalhar com cópia dentro da área privada;
- não registrar razão social, identificadores fiscais, nomes de pessoas ou trechos de relatórios em código, fixtures, documentação, snapshots ou logs;
- descartar nomes individuais da planilha antes do modelo normalizado;
- manter somente os campos indispensáveis à compilação;
- não reescrever nem completar conteúdo técnico ausente;
- exportar auditoria sem caminhos absolutos, bytes ou nomes originais;
- usar dados sintéticos obviamente fictícios em testes públicos;
- informar ao usuário que o processamento hospedado envia os arquivos ao backend.

## Ciclo hospedado

1. a API cria um identificador imprevisível;
2. cada upload recebe um nome interno;
3. tamanho, extensão, tipo real e estrutura são validados;
4. o processamento ocorre em `/tmp/aep-jobs/<job_id>/`;
5. o frontend recebe o DOCX completo como `Blob`;
6. o frontend solicita `DELETE /api/jobs/{id}`;
7. o endpoint `/download` pode limpar o job depois da resposta;
8. a rotina de TTL tenta remover jobs abandonados em até 900 segundos.

O serviço não usa banco de dados, disco persistente ou histórico de documentos. Reinícios descartam o estado em memória. A exclusão da aplicação e o filesystem efêmero reduzem retenção, mas não constituem garantia de apagamento forense de infraestrutura administrada pelo provedor.

## Controles em produção

- HTTPS entre navegador, Pages e backend;
- `AEP_ALLOWED_ORIGINS=https://kinhoog.github.io`;
- `AEP_REQUIRE_ORIGIN=true`;
- CORS sem curinga;
- `Cache-Control: no-store`;
- uma pasta exclusiva por job;
- limites individual e total;
- validação de tipo real e expansão ZIP;
- rejeição de macro, travessia e relações externas perigosas;
- LibreOffice headless em perfil isolado;
- identificadores aleatórios;
- mensagens sem caminhos internos;
- usuário não root no container;
- nenhuma área privada copiada para a imagem;
- limpeza explícita e periódica.

CORS não autentica a API. O MVP não possui login, portanto a URL do backend não deve ser descrita como privada.

## Logs e erros

É permitido registrar:

- identificador aleatório do job;
- estágio e duração;
- código de validação;
- classe genérica da exceção;
- contagens agregadas não identificáveis.

É proibido registrar:

- conteúdo extraído;
- nomes de pessoas ou empresas;
- identificadores cadastrais;
- caminhos privados completos;
- nomes originais de arquivos;
- imagens, documentos ou templates codificados;
- payloads integrais;
- conteúdo de Secret Files.

Mensagens ao navegador devem orientar a correção sem revelar caminhos internos ou material confidencial.

## Template hospedado

O gabarito aprovado permanece fora do Git. `scripts/prepare_private_template.py` cria uma cópia saneada e parametrizada; `scripts/prepare_hosted_template_secret.py` valida essa cópia e produz três arquivos Base64 em `private_templates/hosted_secret/`.

No backend:

1. os Base64 são montados como Secret Files;
2. o startup decodifica o template, o manifesto e o perfil em diretório temporário;
3. hashes, estrutura, marcadores e capacidade são validados;
4. a pipeline permanece indisponível quando a validação falha.

O conjunto medido ocupa 918.504 bytes, abaixo do limite de 1 MiB aplicado pelo script. Se uma nova versão ultrapassar o limite, use outra configuração privada com capacidade suficiente; não publique o template como alternativa.

## Checklist antes de commit

Execute:

```powershell
git status --short
git diff --cached --name-only
git diff --cached
git check-ignore -v private_templates\aep_template.docx
git check-ignore -v private_templates\hosted_secret\aep_template.docx.b64
git check-ignore -v uploads\arquivo.docx
git check-ignore -v outputs\resultado.docx
```

Depois:

- confirme que nenhum arquivo real, Secret File ou artefato gerado aparece;
- revise Markdown, JavaScript e fixtures para nomes, identificadores e conteúdo copiado;
- confirme que `frontend/config.js` contém somente a URL pública da API;
- execute testes sintéticos e o build Docker;
- inspecione a imagem para áreas privadas e formatos documentais;
- prepare apenas os arquivos revisados, sem inclusão forçada;
- confira novamente antes do commit e do push.

Uma busca preventiva deve usar termos definidos localmente pelo operador sem imprimir resultados sensíveis em canais compartilhados.

## Fixtures públicas

Uma fixture pública precisa:

- ser criada do zero;
- usar organizações, pessoas, códigos e textos fictícios;
- conter marca visível de “sintético” ou “sem validade”;
- não reproduzir imagens, redações ou metadados de fonte real;
- residir exclusivamente em `tests/fixtures/public_synthetic/`;
- ser revisada por extração de texto e metadados.

Anonimizar parcialmente um documento real não o transforma automaticamente em fixture pública.

## Contas e provedor

- restrinja permissões administrativas do GitHub e Render;
- não use Persistent Disk;
- revise região, termos e retenção de logs do plano contratado;
- mantenha Secret Files fora de mensagens, tickets e logs;
- substitua Secret Files quando o template mudar;
- valide periodicamente o `DELETE` e o TTL com fixtures sintéticas;
- acompanhe atualizações da imagem base, Python e LibreOffice;
- não envie relatórios reais enquanto o health indicar pipeline degradada.

## Se um dado real aparecer no Git

Interrompa o commit, push ou deploy. Se já houve publicação, não tente resolver apenas apagando o arquivo em outro commit:

1. restrinja o acesso quando possível;
2. coordene a remoção do histórico;
3. remova artefatos de Actions, Pages e imagens afetadas;
4. substitua Secret Files ou credenciais expostos;
5. registre o incidente fora do repositório sem replicar os dados.
