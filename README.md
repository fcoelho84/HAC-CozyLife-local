# CozyLife Local

Integração Home Assistant (HACS) para interruptores CozyLife Wi‑Fi **na LAN** — discovery + ligar/desligar sem cloud.

Protocolo: reverse engineering do app CozyLife Android `1.20.24`. Detalhes em [`docs/`](docs/).

## Instalar via HACS (custom repository)

1. HACS → ⋮ → **Custom repositories**
2. Repository: `https://github.com/fcoelho84/HAC-CozyLife-local`
3. Category: **Integration**
4. Add → procure **CozyLife Local** → Download
5. Reinicie o Home Assistant
6. Settings → Devices & services → Add integration → **CozyLife Local**

HA e os dispositivos precisam estar na mesma rede (UDP broadcast `6095` + TCP `5555`).

## Uso

1. Escolha **Discover on LAN** (ou IP manual).
2. Selecione os interruptores encontrados.
3. Cada um vira um `switch.*` (on/off).

## Protocolo (resumo)

| Ação | Como |
|------|------|
| Descobrir | UDP `255.255.255.255:6095` |
| Controlar | TCP `ip:5555` |
| ON | property `"1"=255`, cmd `3` |
| OFF | property `"1"=0`, cmd `3` |

## Docs

| Arquivo | Para |
|---------|------|
| [`docs/AI_CONTEXT.md`](docs/AI_CONTEXT.md) | Agentes / IA |
| [`docs/protocol.md`](docs/protocol.md) | Wire protocol |
| [`docs/architecture.md`](docs/architecture.md) | Arquitetura |
| [`docs/home-assistant.md`](docs/home-assistant.md) | Runtime HA |

## Estrutura HACS

```text
hacs.json
custom_components/cozylife_local/
docs/
README.md
```

Domain: `cozylife_local` · versão: `0.1.0`
