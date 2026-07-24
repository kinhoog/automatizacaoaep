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
