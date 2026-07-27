# Plano de execução

## Estado inicial verificado

- O repositório remoto está vazio e a branch padrão esperada é `main`.
- O diretório solicitado contém sete artefatos privados do piloto e ainda não é um repositório Git.
- O gabarito possui 28 páginas A4, duas seções, cabeçalhos/rodapés próprios, 20 tabelas e imagens inline/flutuantes.
- A fonte Ergo é HTML compatível com Word, apesar da extensão `.doc`, e contém um bloco adicional que exige reconciliação explícita.
- A fonte psicossocial é um DOCX orientado por imagens; os painéis devem ser associados por posição, função visual e GHE.
- O relatório integrado contém análises por GHE, favorabilidade, prioridades, plano de ação e conclusão sem necessidade de criação técnica.
- O renderizador LibreOffice não está instalado no ambiente auditado. A validação inicial usa Word local como fallback e tentará habilitar LibreOffice para a regressão final.

## Estratégia

1. **Privacidade e workspace**
   - Desenvolver sem copiar dados privados para arquivos rastreados.
   - Criar `local_samples/`, `private_templates/`, `uploads/`, `outputs/` e `generated/` como áreas ignoradas.
   - Copiar os originais sem modificá-los e conferir a eficácia do `.gitignore` antes de qualquer preparação de commit.

2. **Modelo e contrato normalizado**
   - Definir modelos Pydantic para empresa, documento, GHEs oficiais, imagens psicossociais, análises técnicas, Ergo, reconciliação, prioridades, plano de ação, conclusão e mensagens de validação.
   - Permitir exportação JSON de auditoria sem incluir nomes individuais nem conteúdo binário.

3. **Extração**
   - Ler a planilha em modo somente leitura, descartando deliberadamente a coluna de nomes.
   - Detectar DOCX/HTML/OLE pelo conteúdo real.
   - Extrair o Ergo HTML preservando ordem, perguntas, respostas, observações e orientações.
   - Extrair imagens do psicossocial na ordem do documento, classificá-las por papel visual e vinculá-las aos GHEs.
   - Extrair os blocos técnicos integrados ou mesclar os dois relatórios separados sem reescrever seu conteúdo.

4. **Validação e reconciliação**
   - Validar extensões, assinatura, tamanho, empresa, população, cobertura dos GHEs, imagens e blocos técnicos.
   - Sugerir correspondências por código/nome, mas exigir confirmação para divergências e itens sem correspondência.
   - Implementar um modo de compatibilidade privado que reproduza explicitamente o conjunto de blocos observado no gabarito, registrando inclusão e omissão excepcionais no relatório; essa regra não será aplicada ao fluxo geral.

5. **Template e montagem Word**
   - Criar uma cópia privada do gabarito e um manifesto de slots estáveis por parte OOXML, parágrafo, tabela e mídia.
   - Preencher capa, competência, CNPJ, GHEs, datas, Ergo, psicossocial, análises, favorabilidade, prioridades, plano, conclusão e encerramento.
   - Não criar espaço quando a logo estiver ausente e manter `Evolução/Registros` em branco.
   - Corrigir o bookmark quebrado do sumário, preservar campos e ativar `updateFields`.
   - Provar uso real das entradas com teste de mutação.

6. **Aplicação web**
   - Implementar FastAPI com jobs aleatórios, armazenamento temporário isolado, limpeza por TTL e rotas de validação, geração, status e download.
   - Construir interface responsiva com uploads, dois modos de análise, resumo de GHEs, reconciliação, progresso e downloads.
   - Manter todo o processamento local e logs sem conteúdo confidencial.

7. **Testes e regressão**
   - Gerar fixtures públicas sintéticas e cobrir extratores, validação, reconciliação, segurança, montagem, limpeza e endpoints.
   - Executar o piloto privado ponta a ponta e gerar DOCX, relatório JSON e comparação estrutural/visual em diretório ignorado.
   - Renderizar gabarito e saída, revisar todas as páginas e documentar diferenças relevantes.

8. **Entrega Git**
   - Fazer commits pequenos após testes e inspeção dos arquivos preparados.
   - Executar varredura por dados privados e revisar o diff final.
   - Copiar a árvore e o histórico para o diretório solicitado, validar o estado ignorado dos originais, configurar `origin` e enviar `main`.

## Critério do MVP

O MVP está aceito quando valida os uploads, identifica e reconcilia GHEs, gera um DOCX editável com todos os blocos obrigatórios, passa os testes sintéticos e o piloto privado, produz a comparação, inicia no Windows por PowerShell e não prepara nenhum dado privado para commit.

## Resultado da primeira execução

- Fluxo FastAPI e interface web implementados de ponta a ponta, incluindo os dois modos de análise, reconciliação explícita e downloads.
- Fixtures públicas sintéticas criadas e identificadas como sem validade; a suíte cobre extração, validação, segurança, montagem, mutação, limpeza e API.
- Piloto privado executado em modo de compatibilidade, com os quatro blocos Ergo auditados e a exceção visual mantida somente no relatório local.
- Saída e gabarito renderizados pelo LibreOffice 26.2.4: 28 páginas em ambos, sem página em branco inesperada e com a mesma contagem de seções, parágrafos, tabelas e mídias.
- Fluxo real da interface validado em navegador com arquivos sintéticos: upload sem logo, resumo dos GHEs, escolha de não aplicável, geração e download do DOCX.
- Áreas privadas, temporárias, saídas e runtime do renderizador permanecem ignorados pelo Git.

## Verificação final do MVP

- O template privado foi saneado sem alterar o original; o manifesto registra 287 marcadores, mídia neutra, hash e capacidade de três GHEs. A montagem rejeita manifesto ausente, adulteração, resíduo e excesso de capacidade.
- O modo de compatibilidade só é aceito com perfil privado vinculado criptograficamente às fontes exatas; o piloto registrou três blocos Ergo incluídos e um omitido.
- A suíte final passou com 55 testes. Também passaram `compileall`, validação sintática do JavaScript e a geração/download pela interface em viewport desktop e móvel.
- O piloto privado produziu um DOCX editável com 28 páginas, duas seções, 334 parágrafos, 20 tabelas, 10 imagens inline e nenhum marcador residual.
- A comparação LibreOffice encontrou a mesma estrutura (`2/334/20/10/12`) e 28 páginas nos dois documentos, similaridade textual de 98,32%, presença textual de 96,73% e média de 5,13% de pixels alterados, sem cortes, sobreposições ou páginas em branco inesperadas.
- O teste HTTP real confirmou validação, quatro blocos Ergo detectados, reconciliação, geração, download e descarte do modelo temporário; o teste de mutação confirmou que alterações sintéticas nas entradas mudam a saída.

## Plano da migração para entrega hospedada

1. **Separar a interface**
   - Extrair a experiência pública para `frontend/`, mantendo HTML, CSS e JavaScript independentes do FastAPI.
   - Usar apenas caminhos relativos compatíveis com `/automatizacaoaep/`.
   - Resolver a origem da API por `window.AEP_CONFIG.API_BASE_URL`, sem credenciais no cliente.

2. **Publicar no GitHub Pages**
   - Criar um workflow acionado por push em `main`.
   - Gerar `config.js` com a variável pública `AEP_API_BASE_URL`.
   - Publicar somente `frontend/` pelas ações oficiais do Pages.

3. **Adaptar o runtime Python**
   - Preservar a pipeline, os extratores, a reconciliação e o montador Word existentes.
   - Aceitar `PORT`, escutar em `0.0.0.0` e restringir CORS à origem oficial.
   - Aplicar `Cache-Control: no-store` e exigir origem permitida nas rotas operacionais.

4. **Reduzir a retenção**
   - Criar cada job em `/tmp/aep-jobs/<job_id>/`.
   - Receber o DOCX como `Blob` antes de solicitar `DELETE /api/jobs/{id}`.
   - Oferecer `/download` com limpeza posterior à resposta.
   - Executar limpeza na inicialização, periodicamente e após 900 segundos para jobs abandonados.

5. **Proteger o template**
   - Manter template, manifesto, perfil e Base64 em `private_templates/`.
   - Validar e preparar três Secret Files com `scripts/prepare_hosted_template_secret.py`.
   - Decodificar no startup, conferir hash e manifesto e falhar de modo fechado.
   - Não copiar material privado para imagem, frontend, release ou histórico.

6. **Empacotar e hospedar**
   - Criar uma imagem com Python, LibreOffice headless, fontes e usuário não root.
   - Declarar um único Web Service Docker no `render.yaml`, sem banco ou disco persistente.
   - Usar health check para só liberar a pipeline validada.

7. **Testar**
   - Preservar a suíte existente e cobrir CORS, `PORT`, Base64, hash inválido, downloads, exclusão, TTL, Pages e cache.
   - Construir e inspecionar a imagem no CI, confirmando a ausência de material privado.
   - Reexecutar a regressão privada estrutural e visual antes da publicação.

8. **Publicar e verificar**
   - Enviar código e workflows ao repositório.
   - Criar o Blueprint no Render e cadastrar os Secret Files por canal privado.
   - Configurar `AEP_API_BASE_URL`, republicar o Pages e testar o fluxo público.
   - Confirmar exclusão explícita e expiração sem afirmar garantias forenses do provedor.

## Resultado preparado da migração hospedada

- A arquitetura pública está separada entre GitHub Pages e FastAPI, sem mover o compilador para JavaScript.
- O frontend estático preserva o fluxo de dados, uploads, validação, reconciliação, geração, progresso e download.
- O download principal recebe todo o DOCX antes da exclusão explícita; um endpoint alternativo agenda limpeza depois da resposta.
- Jobs usam `/tmp/aep-jobs`, TTL inicial de 900 segundos e varreduras de inicialização e periódica.
- CORS usa lista explícita com `https://kinhoog.github.io`; a configuração de produção não usa curinga.
- O container inclui LibreOffice e fontes, executa como usuário não root, não solicita armazenamento persistente e expõe health check.
- O template hospedado é fornecido por três Secret Files. O conjunto Base64 medido ocupa 918.504 bytes, sob o limite de 1 MiB aplicado pelo preparador.
- O workflow do Pages gera `config.js` a partir de `AEP_API_BASE_URL`; nenhum token ou template faz parte do artefato público.
- O CI cobre a suíte Python, build e inspeção do container. A implantação efetiva exige acesso administrativo ao GitHub Pages e ao Render, além do cadastro privado dos Secret Files.
