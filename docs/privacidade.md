# Privacidade e operação segura

## Princípio

Relatórios empresariais, planilhas, imagens cadastrais, templates oficiais e documentos gerados são confidenciais. Eles permanecem no computador operador, nunca entram no repositório público e nunca são enviados a APIs externas.

## Classificação dos arquivos

| Classe | Exemplos | Git |
| --- | --- | --- |
| Confidencial | relatórios, planilhas, imagens cadastrais, logos e amostras reais | Proibido |
| Template privado | gabarito, template saneado, manifesto e perfil de compatibilidade | Proibido |
| Artefato operacional | uploads, intermediários, DOCX/PDF/JSON gerados e renders | Proibido |
| Público sintético | fixtures criadas do zero, marcadas como sem validade | Permitido somente na pasta autorizada |
| Código e documentação | fonte sem dados reais e instruções genéricas | Permitido após revisão |

As pastas protegidas são:

- `local_samples/`;
- `private_templates/`;
- `uploads/`;
- `generated/`;
- `outputs/`.

O `.gitignore` também bloqueia globalmente extensões de documentos, planilhas e imagens. Apenas `tests/fixtures/public_synthetic/` possui exceções explícitas.

## Regras para dados

- não alterar ou sobrescrever a fonte original;
- trabalhar com cópia dentro da área privada;
- não registrar razão social, identificadores fiscais, nomes de pessoas ou trechos de relatórios em código, fixtures, documentação, snapshots ou logs;
- descartar nomes individuais da planilha antes de criar o modelo normalizado;
- manter somente os campos indispensáveis à compilação;
- não reescrever nem completar conteúdo técnico ausente;
- exportar auditoria sem caminhos absolutos, bytes ou nomes originais de arquivos;
- usar dados sintéticos obviamente fictícios em testes públicos.

## Ciclo de vida de uma execução

1. a API cria um identificador aleatório;
2. cada upload recebe um nome interno conhecido;
3. tamanho, extensão e tipo real são validados;
4. o processamento ocorre no diretório exclusivo do job;
5. downloads usam somente caminhos resolvidos pela aplicação;
6. o job expira pelo TTL configurado;
7. entradas e artefatos temporários são removidos pela limpeza.

O usuário deve baixar o resultado assim que a execução terminar. `uploads/`, `generated/` e `outputs/` não são armazenamento permanente.

## Logs e mensagens de erro

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
- nomes de arquivos recebidos;
- imagens ou documentos codificados;
- payloads integrais.

As mensagens ao navegador devem orientar a correção sem revelar caminhos internos ou detalhes de implementação.

## Checklist antes de commit

Execute na raiz:

```powershell
git status --short
git diff --cached --name-only
git diff --cached
git check-ignore -v private_templates\aep_template.docx
git check-ignore -v private_templates\aep_compatibility_profile.json
git check-ignore -v uploads\arquivo.docx
git check-ignore -v outputs\resultado.docx
```

Depois:

- confirme que nenhum arquivo real aparece como novo, modificado ou preparado;
- revise documentos Markdown e fixtures para nomes, identificadores e conteúdo copiado;
- execute todos os testes sintéticos;
- confirme que saídas, caches e renders continuam ignorados;
- prepare apenas os arquivos revisados, nunca use inclusão forçada;
- confira novamente a lista preparada antes do commit e antes do push.

Uma busca preventiva deve usar termos definidos localmente pelo operador, sem imprimir resultados sensíveis em canais compartilhados.

## Criação de fixtures públicas

Uma fixture pública precisa:

- ser criada do zero;
- usar organização, pessoas, códigos, números e textos fictícios;
- conter uma marca visível de “sintético” ou “sem validade”;
- não reproduzir imagens, redações técnicas ou metadados de fonte real;
- residir exclusivamente em `tests/fixtures/public_synthetic/`;
- ser revisada visualmente e por extração de texto e metadados.

Anonimizar parcialmente um documento real não o transforma automaticamente em fixture pública.

## Template

O gabarito aprovado é lido como fonte privada e nunca modificado. `scripts/prepare_private_template.py` cria outra cópia saneada e parametrizada e um manifesto de slots em `private_templates/`. O saneamento reduz o risco de conteúdo residual, mas não transforma o template em arquivo público: identidade visual, estrutura e metadados operacionais continuam privados.

Ao trocar o gabarito:

1. preserve a versão original fora do Git;
2. gere um novo template privado;
3. confira hashes, marcadores, mídia neutra, capacidade e contagens do manifesto;
4. rode testes sintéticos;
5. rode a regressão privada estrutural e visual;
6. não prepare template, manifesto, PDFs ou imagens para commit.

Perfis de compatibilidade também permanecem em `private_templates/`. Eles devem conter somente hashes e ordinais auditáveis, nunca nomes, identificadores, caminhos de origem ou trechos dos relatórios.

## Estação de trabalho

- mantenha o servidor em `127.0.0.1`;
- não sincronize pastas privadas com repositórios públicos;
- restrinja permissões do diretório do projeto ao operador;
- mantenha Windows, Python e LibreOffice atualizados;
- encerre o servidor quando não estiver em uso;
- remova jobs expirados e arquivos que não tenham mais finalidade operacional;
- use os controles de backup e descarte definidos pela organização.

## Se um dado real aparecer no Git

Interrompa o commit ou push imediatamente. Se ainda não houve commit, retire o arquivo da área preparada e confirme as regras de ignore. Se houve commit local ou publicação remota, não tente apenas apagar o arquivo em um commit posterior: trate como incidente, restrinja o acesso quando possível e coordene a reescrita do histórico e a eventual rotação de identificadores com o responsável pelo repositório.

Registre o incidente fora do repositório, sem replicar os dados expostos.
