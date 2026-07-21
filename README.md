# Jobel Croquis Engine

Motor para interpretar projetos elétricos e gerar croquis RGE/CPFL em Excel editável e PDF.

O foco deste repositório é o engine. Autenticação, organização por cidades e o editor visual serão integrados depois que a regressão técnica do croqui estiver aprovada.

## Fluxo técnico

O fluxo é fixo:

1. o backend extrai texto, palavras, coordenadas e vetores do projeto PDF;
2. a pré-análise local produz evidências, candidatos e diagnóstico, mas não vira silenciosamente o croqui final;
3. a análise visual obrigatória do backend recebe o PDF e até três pares oficiais semelhantes;
4. a proposta estruturada passa pela validação determinística e não pode inventar identificadores;
5. os símbolos são clonados como objetos DrawingML da aba `Simbologia` do Excel oficial;
6. o PDF é exportado a partir do mesmo `.xlsx` editável.

O navegador não recebe chave, provedor, modelo, contagem de tokens, IDs de resposta ou caminhos do
corpus. Se a análise backend obrigatória estiver indisponível, o sistema bloqueia a geração em vez de
produzir um croqui local de baixa confiança.

Não existe substituição por SVG, ícone aproximado ou imagem raster. Se um objeto oficial estiver ausente, a geração falha explicitamente.

O usuário envia **somente o projeto elétrico em PDF**. O modelo oficial sanitizado fica versionado em `backend/assets/modelo_croqui_oficial.xlsx`; a interface e a API não aceitam upload de modelo ou simbologia.

## Símbolos exigidos no modelo

| Elemento | Nome interno oficial |
|---|---|
| TR | `AutoShape 238` |
| FU | `Group 729` |
| FC | `Group 302` |
| RL | `Text Box 245` |
| RG | `Group 260` |
| OL | `Text Box 261` |
| SC | `Text Box 247` |
| Poste | `Group 184` |
| Área de trabalho | `Rectangle 334` |
| Rede secundária — contínua | `Line 429` |
| Rede primária — tracejada | `Line 430` |
| Rede projetada — marrom | `Line 431` |

O logo RGE já existente na aba de croqui é preservado sem reconstrução. O asset interno não contém diagrama, identificadores ou dados de uma obra usada como referência.

## Executar com Docker

```bash
cp .env.example .env
docker compose up --build
```

Acesse <http://localhost:8080>. A documentação da API fica desabilitada por padrão; em desenvolvimento, use `EXPOSE_API_DOCS=true` para habilitar `/api/docs`.

Preencha a chave somente no `.env` do backend:

```dotenv
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-sol
AI_ENABLED=true
AI_REQUIRED=true
```

A chave não é enviada ao navegador, não aparece nas respostas e não é gravada nos relatórios.

## Corpus oficial

Os 155 diretórios devem ficar em `CROQUI IA/` na raiz. Essa pasta é ignorada pelo Git e pelo contexto
de build para não publicar os projetos em um repositório público; o Docker a monta em modo somente
leitura no backend. No primeiro start, o sistema cria um manifesto determinístico em `/data/training`.

O split padrão é por grupo de equipamento, evitando que casos do mesmo equipamento caiam em treino e
teste: 70% treino, 10% validação e 20% teste. O runtime recupera exemplos somente de treino/validação e
nunca usa um projeto idêntico ao PDF de entrada.

Comandos de preparação, rotulagem, revisão e fine-tuning estão documentados em
[`docs/CORPUS_E_IA.md`](docs/CORPUS_E_IA.md).

## Cadastro de rede interno

Alguns projetos mostram apenas o transformador da intervenção; o número do fusível ou religador a montante não existe no PDF. Nesses casos, nenhum modelo de IA consegue determinar o número exato com segurança sem o cadastro da rede.

Quando existir uma base corporativa, ela pode ser configurada exclusivamente no backend com `NETWORK_REGISTRY_PATH`. Não há upload desse arquivo na tela. O arquivo interno aceita `.csv`, `.xls` ou `.xlsx`, e a primeira linha deve identificar estas informações (os nomes abaixo e equivalentes em inglês são reconhecidos):

| Informação | Exemplo de coluna |
|---|---|
| Equipamento de referência/jusante | `equipamento referencia` |
| Tipo do isolamento | `tipo isolamento` |
| Número do isolamento a montante | `numero isolamento` |
| Município, opcional | `municipio` |

Exemplo:

```csv
equipamento referencia;tipo isolamento;numero isolamento;municipio
800001;FU;900001;Cidade Exemplo
```

O vínculo cadastral é evidência local de alta confiança e também é enviado como contexto controlado ao fallback.

## Estados do motor

- `GENERATED`: validação aprovada e Excel/PDF gerados;
- `NEEDS_REVIEW`: identificação ou topologia bloqueada;
- `READY_TO_GENERATE`: plano validado antes da exportação.

## Artefatos

Quando aprovado, cada job contém:

- `.xlsx` nativo e editável;
- `.xls` para compatibilidade operacional;
- `.pdf` exportado do próprio Excel;
- `.png` de conferência visual;
- `relatorio.json` com evidências e validações.

Downloads: `GET /api/jobs/{job_id}/download/{pdf|xls|xlsx|preview|report}`.

## Desenvolvimento e testes

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
ruff check backend tests
```

Os testes validam o modelo interno real: presença dos objetos oficiais da aba `Simbologia`, preservação
do logo RGE, remoção dos dados da obra de origem, clonagem nativa, privacidade do backend e isolamento
entre as partições do corpus.

Para atualizar o asset a partir de outro croqui oficial já convertido para `.xlsx`:

```bash
python -m scripts.build_official_template /caminho/croqui-oficial.xlsx
```

O sanitizador mantém a aba `Simbologia` byte a byte, preserva somente as imagens do cabeçalho na aba `Croqui` e remove o diagrama e os dados do projeto de origem.
