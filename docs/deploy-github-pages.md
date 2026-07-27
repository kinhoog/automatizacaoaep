# Implantação do frontend no GitHub Pages

## Objetivo

Publicar somente os arquivos estáticos de `frontend/` em:

[https://kinhoog.github.io/automatizacaoaep/](https://kinhoog.github.io/automatizacaoaep/)

O frontend não executa Python, FastAPI ou LibreOffice. Ele envia os arquivos por HTTPS ao backend configurado e recebe o DOCX como `Blob`.

## Pré-requisitos

- repositório `kinhoog/automatizacaoaep` com a branch `main`;
- GitHub Pages habilitado com **GitHub Actions** como fonte;
- backend já disponível em uma URL HTTPS;
- permissão para configurar variáveis e Pages no repositório.

Documentação oficial:

- [Usar um workflow personalizado com GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages);
- [Configurar uma origem de publicação para o GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site);
- [Variáveis em workflows](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-variables).

## Configurar a URL do backend

No repositório:

1. abra **Settings**;
2. entre em **Secrets and variables → Actions**;
3. selecione **Variables**;
4. crie ou altere a variável `AEP_API_BASE_URL`;
5. informe apenas a origem HTTPS do backend, sem credenciais e sem barra final, por exemplo:

```text
https://servico-exemplo.onrender.com
```

Essa informação é pública por definição: ela será gravada no JavaScript entregue ao navegador. Não use um secret para a URL e não inclua tokens, chaves, usuário ou senha.

## Publicar

O workflow [`.github/workflows/deploy-pages.yml`](../.github/workflows/deploy-pages.yml) é executado em cada push para `main` e também pode ser iniciado manualmente.

Ele:

1. baixa o repositório;
2. lê `vars.AEP_API_BASE_URL`;
3. valida que a URL usa HTTPS e não contém credenciais;
4. gera `frontend/config.js`;
5. configura o Pages;
6. empacota somente `frontend/`;
7. publica o artefato no ambiente `github-pages`;
8. registra a URL resultante na execução.

Se `AEP_API_BASE_URL` estiver vazia, o build ainda publica a interface, mas `config.js` recebe uma URL vazia. Nesse estado a página não envia arquivos e apresenta uma mensagem de configuração pendente; ela não usa um backend fictício.

## Compatibilidade com o subdiretório

Todos os arquivos do frontend usam referências relativas:

```html
<link rel="stylesheet" href="./styles.css">
<script src="./config.js"></script>
<script src="./app.js"></script>
```

Não adicione caminhos iniciados por `/`, porque eles apontariam para a raiz `kinhoog.github.io/` em vez de `/automatizacaoaep/`.

As chamadas da API são montadas a partir de `window.AEP_CONFIG.API_BASE_URL`; o caminho do repositório do Pages não é acrescentado à URL do backend.

## Validar a publicação

Após a conclusão do workflow:

1. abra a URL indicada no job `Publicar`;
2. confirme no navegador que `index.html`, `styles.css`, `config.js` e `app.js` respondem sem erro;
3. confira que a página permanece em `/automatizacaoaep/`;
4. teste em viewport desktop e móvel;
5. confirme que a mensagem de serviço indisponível não aparece quando a API está saudável;
6. valide um conjunto sintético;
7. baixe o relatório de validação, se necessário;
8. gere e baixe o DOCX;
9. confirme que o frontend solicitou `DELETE /api/jobs/{id}` depois de receber todo o documento.

O Pages fornece HTTPS para o site publicado. A URL do backend também precisa usar HTTPS; o workflow rejeita outro protocolo.

## Atualizar uma versão

1. altere e teste o código;
2. revise o diff e faça a varredura de privacidade;
3. envie a alteração para `main`;
4. aguarde os workflows de testes e Pages;
5. se a URL do backend mudou, atualize `AEP_API_BASE_URL`;
6. execute novamente **Publicar frontend no GitHub Pages**;
7. repita a validação pública.

## Diagnóstico

| Sintoma | Verificação |
| --- | --- |
| Página abre, mas a API está “não configurada” | confira `AEP_API_BASE_URL` e execute novamente o workflow |
| Recursos retornam 404 | procure caminhos absolutos no HTML/CSS/JS |
| Navegador bloqueia a API por CORS | confirme `AEP_ALLOWED_ORIGINS=https://kinhoog.github.io` no backend |
| Workflow não publica | confirme Pages com fonte GitHub Actions e permissões `pages: write` e `id-token: write` |
| URL do backend é rejeitada no build | use uma origem HTTPS sem usuário, senha, query ou fragmento |

Não copie template, relatórios, documentos gerados ou segredos para `frontend/`. Tudo nessa pasta é público depois do deploy.
