# Regras permanentes

- Nunca alterar os documentos originais nem versionar amostras, templates privados, uploads, saídas ou dados empresariais reais.
- Todo arquivo público de teste deve ser sintético, identificado como sem validade e residir em `tests/fixtures/public_synthetic/`.
- A planilha de GHEs é a fonte de verdade para código, nome, setores, cargos e população; nomes individuais nunca entram no modelo normalizado ou nos logs.
- O compilador não cria conclusões técnicas: extrai e posiciona somente conteúdo aprovado dos relatórios.
- Divergências de GHE exigem decisão explícita e rastreável; nenhuma renumeração, exclusão ou correção silenciosa.
- O gabarito é somente uma referência privada. A saída deve ser gerada a partir dos uploads e do manifesto de slots do template.
- Validar tipo real, tamanho e conteúdo antes de processar; nunca aceitar caminhos fornecidos pelo cliente.
- Manter testes unitários e de integração com dados sintéticos. Antes de commit: testes, `git status`, diff preparado e varredura de privacidade.
- Toda alteração de DOCX relevante termina com auditoria estrutural e renderização visual quando houver um renderizador disponível.
