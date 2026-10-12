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
3. Informe **quantos canais/teclas** cada um tem (`1` = tomada / 1 tecla; `2+` = multi-tecla).
4. Cada canal vira um `switch.*` separado.

Já instalou? Em **Configure** da integração, ajuste o número de canais por dispositivo e recarregue.

## Multi-tecla (importante)

No CozyLife, interruptor de várias teclas usa o DPID `"1"` como **bitmask**:

| Valor | Efeito |
|-------|--------|
| `1` (`0x01`) | só tecla 1 |
| `2` (`0x02`) | só tecla 2 |
| `3` (`0x03`) | teclas 1+2 |
| `255` | **todas** as teclas |

A integração liga/desliga um bit por entidade e preserva os outros.

## Protocolo (resumo)

| Ação | Como |
|------|------|
| Descobrir | UDP `255.255.255.255:6095` |
| Controlar | TCP `ip:5555` |
| ON (1 canal) | property `"1"=255`, cmd `3` |
| OFF | property `"1"=0`, cmd `3` |
| Multi-tecla | property `"1"` = bitmask, cmd `3` |

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

Domain: `cozylife_local` · versão: `0.2.0`
