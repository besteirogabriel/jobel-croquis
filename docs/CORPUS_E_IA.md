# Corpus, avaliação e fine-tuning

Toda a operação acontece no backend do próprio projeto. O chat não é dependência do runtime.

O processamento normal do MVP pode usar `AI_PROVIDER=codex`, autenticado com a conta ChatGPT pelo
serviço `codex-login`. Rotulagem em lote e fine-tuning continuam sendo operações da API e, portanto,
exigem `OPENAI_API_KEY` quando forem executados.

## 1. Preparar o corpus

Coloque os pares em `CROQUI IA/<caso>/`. Cada caso deve conter um projeto PDF, o croqui oficial PDF e
o Excel oficial `.xls` ou `.xlsx`.

```bash
python -m backend.training.cli prepare
python -m backend.training.cli summary
```

O manifesto fica em `TRAINING_DIR/manifest.json`. Arquivos originais não são modificados. O algoritmo
mantém o mesmo tipo+número de equipamento em apenas uma partição e reserva `test` para avaliação cega.

## 2. Criar e revisar ground truth

A rotulagem compara o projeto ao croqui oficial e cria um `CroquiPlan` em estado `draft`:

```bash
python -m backend.training.cli label --case ID_DO_CASO
```

Um engenheiro deve revisar antes da aprovação:

```bash
python -m backend.training.cli review \
  --case ID_DO_CASO \
  --status approved \
  --reviewer RESPONSAVEL_TECNICO
```

Rótulos com divergência de equipamento, quantidade incorreta de equipamento principal ou ausência de
topologia não são aprovados sem uma decisão explícita do revisor.

## 3. Materializar datasets

```bash
python -m backend.training.cli build
```

São produzidos `train.jsonl`, `validation.jsonl` e `test-evals.jsonl`. O comando de submissão aceita
somente treino e validação; teste permanece local para medir generalização.

## 4. Submeter fine-tuning

Fine-tuning é opcional e exige confirmação consciente no `.env`:

```dotenv
FINE_TUNING_ENABLED=true
FINE_TUNING_BASE_MODEL=<modelo-habilitado-na-conta>
```

Depois:

```bash
python -m backend.training.cli submit \
  --train /data/training/datasets/<fingerprint>/train.jsonl \
  --validation /data/training/datasets/<fingerprint>/validation.jsonl

python -m backend.training.cli status --job ftjob-...
```

Quando houver um modelo concluído e validado, configure `OPENAI_FINE_TUNED_MODEL`. Se estiver vazio, o
runtime usa `OPENAI_MODEL` com recuperação dos exemplos oficiais. A disponibilidade de fine-tuning para
entradas visuais depende dos modelos e permissões existentes na conta; não habilite o envio antes de
confirmar isso.

## 5. Regras de segurança

- a autenticação do MVP fica somente no volume Docker `codex_auth`;
- `OPENAI_API_KEY`, quando usada para treinamento ou produção, existe somente no `.env` do servidor;
- `CROQUI IA/`, imagens de referência, rótulos e datasets não entram no Git.
- o frontend não recebe estado de IA, modelo, tokens, IDs ou caminhos internos;
- os símbolos nunca são gerados pela IA: são clonados do Excel oficial;
- falha da análise obrigatória bloqueia o croqui;
- exemplos da partição `test` nunca entram no prompt de produção.
