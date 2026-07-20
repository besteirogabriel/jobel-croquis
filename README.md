# Jobel Croquis Engine

Motor local-first para interpretar projetos elétricos e gerar croquis RGE/CPFL em Excel editável e PDF.

O foco deste repositório é o engine. Autenticação, organização por cidades e o editor visual serão integrados depois que a regressão técnica do croqui estiver aprovada.

## Regra de decisão

O fluxo é fixo:

1. o backend extrai texto, palavras, coordenadas e vetores do projeto PDF;
2. o motor local classifica o equipamento de isolamento e reconstrói a topologia;
3. a validação bloqueia número ausente, baixa confiança, ambiguidade e rede fragmentada;
4. somente se o plano local for bloqueado, e a integração estiver habilitada, o PDF é enviado à OpenAI;
5. a proposta externa passa novamente pela validação local e não pode inventar identificadores;
6. os símbolos são clonados como objetos DrawingML da aba `Simbologia` do Excel oficial;
7. o PDF é exportado a partir do mesmo `.xlsx` que o engenheiro poderá editar.

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
| Poste | `Oval 148` |
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

Para habilitar o fallback da OpenAI, preencha no `.env` do backend:

```dotenv
AI_ENABLED=true
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-sol
```

A chave não é enviada ao navegador, não aparece nas respostas e não é gravada nos relatórios. Com `AI_ENABLED=false`, nenhuma chamada externa é realizada.

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
770053;FU;1130054;Caxias do Sul
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

Os testes validam o modelo interno real: presença de todos os objetos oficiais da aba `Simbologia`, preservação do logo RGE, remoção dos dados da obra de origem e clonagem nativa dos objetos para um novo croqui.

Para atualizar o asset a partir de outro croqui oficial já convertido para `.xlsx`:

```bash
python -m scripts.build_official_template /caminho/croqui-oficial.xlsx
```

O sanitizador mantém a aba `Simbologia` byte a byte, preserva somente as imagens do cabeçalho na aba `Croqui` e remove o diagrama e os dados do projeto de origem.
