# Jobel Croquis

Aplicação local para analisar projetos elétricos e preparar a geração de croquis.

## Executar

```bash
cp .env.example .env
docker compose up --build
```

Acesse `http://localhost:8080`.

## Segurança

A chave `OPENAI_API_KEY` existe somente no container backend. O frontend não recebe chave, prompt, modelo ou respostas brutas. Com `AI_ENABLED=false`, todo o fluxo funciona em modo offline.

## Regra operacional crítica

O equipamento da tabela de manobras não é automaticamente o equipamento que nomeia o croqui. Quando o PDF não contém o dispositivo de isolamento, a interface exige confirmação ou cadastro de rede.
