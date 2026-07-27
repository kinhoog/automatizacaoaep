# Retenção e privacidade na versão hospedada

## Escopo

A versão pública envia os arquivos do navegador para um backend hospedado. Portanto, os documentos deixam a estação do usuário durante a execução e são processados na infraestrutura temporária do provedor. Eles não são enviados pela aplicação a APIs de análise, modelos de inteligência artificial ou serviços externos de armazenamento.

Não há cadastro de empresas, banco de dados, histórico de documentos, disco persistente ou backup de uploads implementado pelo sistema.

## Aviso exibido na interface

> Os arquivos são utilizados somente durante a geração do documento. Não há banco de dados ou armazenamento permanente. Após o download, os arquivos da execução são excluídos automaticamente.

Esse texto resume o comportamento da aplicação. A exclusão pode ocorrer por solicitação explícita depois do download, por limpeza agendada após uma resposta de download ou pelo TTL. Ela não deve ser interpretada como garantia de apagamento forense de todas as camadas administradas pelo provedor.

## Dados processados

A execução pode receber:

- dados cadastrais informados no formulário;
- planilha oficial de GHEs;
- relatórios técnicos e de diagnóstico;
- cartão cadastral;
- logo opcional;
- decisões de reconciliação;
- documento e relatório de validação gerados.

Nomes individuais encontrados na planilha não entram no modelo normalizado nem nos logs. O sistema deve manter apenas os campos necessários para compilar o documento.

## Trânsito

O frontend é entregue por HTTPS pelo GitHub Pages e aceita somente uma URL de backend HTTPS. O Render encerra a conexão TLS antes de encaminhar a requisição ao container.

CORS permite a origem pública `https://kinhoog.github.io` e não usa curinga em produção. Esse controle reduz chamadas indevidas feitas por páginas em navegadores, mas não autentica usuários nem impede clientes externos de construir requisições.

## Armazenamento temporário

Cada job recebe um identificador aleatório e uma pasta exclusiva:

```text
/tmp/aep-jobs/<job_id>/
```

O backend mantém metadados operacionais e o modelo normalizado em memória. O filesystem do container é efêmero e nenhum Persistent Disk deve ser anexado ao serviço.

As áreas privadas locais do repositório continuam ignoradas:

- `local_samples/`;
- `private_templates/`;
- `uploads/`;
- `generated/`;
- `outputs/`.

Somente fixtures criadas do zero e identificadas como sintéticas podem ser versionadas em `tests/fixtures/public_synthetic/`.

## Fluxo de remoção

### Download preferencial

1. o frontend solicita `GET /api/jobs/{id}/document`;
2. aguarda o corpo completo da resposta;
3. cria um `Blob` não vazio;
4. inicia o download no navegador;
5. chama `DELETE /api/jobs/{id}`;
6. o backend remove o diretório e o estado do job.

O relatório de validação deve ser consultado ou baixado antes do Word, porque o job deixa de existir após a exclusão.

### Download com limpeza após a resposta

`GET /api/jobs/{id}/download` oferece uma alternativa. A remoção é registrada como tarefa posterior à resposta, para não apagar o arquivo antes do envio do DOCX.

### Jobs abandonados

`AEP_JOB_TTL_SECONDS=900` limita inicialmente a retenção a 15 minutos. Uma varredura na inicialização e uma rotina periódica tentam remover jobs vencidos, incluindo casos em que:

- a página foi fechada;
- a conexão caiu;
- a validação não foi seguida de geração;
- o documento foi gerado, mas não baixado;
- o frontend não conseguiu enviar o `DELETE`.

Falhas transitórias de remoção podem adiar a limpeza até uma nova tentativa. Uma reinicialização elimina o estado em memória e o armazenamento efêmero do provedor não deve ser usado como arquivo permanente.

## Cache

As respostas da API usam:

```text
Cache-Control: no-store
```

Isso orienta navegadores e intermediários a não armazenar as respostas. O frontend também usa `cache: "no-store"` nas requisições. Cabeçalhos de cache são controles de comportamento HTTP, não uma prova de eliminação física em todas as camadas.

## Validação de entrada

Antes do processamento, o backend aplica:

- limite individual e limite total da requisição;
- lista positiva de extensões por campo;
- detecção do tipo real;
- inspeção de estruturas DOCX/XLSX;
- limite de arquivos e expansão de pacotes ZIP;
- rejeição de macros;
- rejeição de travessia de diretório;
- rejeição de relações externas perigosas;
- assinatura válida para PNG, JPEG e WebP;
- nomes internos sanitizados;
- diretório controlado pela aplicação;
- conversão de `.doc` binário pelo LibreOffice headless com perfil isolado.

O cliente nunca fornece um caminho de filesystem para ser aberto diretamente pelo servidor.

## Logs

Pode ser registrado:

- identificador aleatório do job;
- estágio e duração;
- código de validação;
- classe genérica de erro;
- contagens agregadas necessárias à operação.

Não deve ser registrado:

- conteúdo extraído;
- nome de empresa ou pessoa;
- identificador cadastral;
- nome original de arquivo;
- caminho privado;
- payload de upload;
- imagem ou documento em Base64;
- conteúdo do template ou dos Secret Files.

Mensagens ao navegador devem explicar a correção necessária sem revelar caminhos internos ou detalhes confidenciais.

## Template privado

O template saneado, o manifesto e o perfil de compatibilidade:

- não entram no repositório;
- não entram no frontend;
- não entram na imagem Docker;
- são preparados localmente como Base64;
- são cadastrados como Secret Files;
- são decodificados em área temporária;
- têm integridade e estrutura verificadas antes de a pipeline ficar pronta.

O conjunto atual mede 918.504 bytes em Base64. Se ultrapassar o limite de 1 MiB adotado na preparação, deve ser usado outro mecanismo privado com capacidade suficiente. Publicar o template para contornar o limite não é aceitável.

## Responsabilidades operacionais

Antes de disponibilizar o serviço:

1. revise os termos, a região e as políticas de retenção de logs do provedor;
2. restrinja o acesso administrativo às contas do GitHub e Render;
3. não habilite discos persistentes;
4. mantenha os Secret Files fora de canais compartilhados;
5. valide exclusão explícita e TTL com dados sintéticos;
6. confirme que o CI não encontra documentos privados na imagem;
7. revise periodicamente dependências, imagem base e configuração CORS;
8. comunique aos usuários que os arquivos são enviados para processamento hospedado.

## Incidente

Se um arquivo real ou secret aparecer no Git:

1. interrompa o push, deploy ou publicação;
2. restrinja o acesso ao repositório quando possível;
3. não tente resolver apenas com um commit posterior;
4. coordene a remoção do histórico e a rotação dos secrets;
5. revise imagens, caches, artefatos de Actions e Pages;
6. registre o incidente em canal apropriado sem replicar o conteúdo.

Se um job não for removido dentro do TTL esperado, retire o serviço de uso, preserve apenas metadados operacionais não confidenciais para diagnóstico e corrija a rotina antes de retomar uploads reais.
