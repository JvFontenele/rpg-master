---
name: combate
description: Combate mecânico persistente com as regras do sistema da campanha — iniciar, ver estado, resolver ações (ataque, movimento, ajuda, estabilizar…), avançar turno, encerrar, retomar e consultar o log.
argument-hint: iniciar | status | agir | proximo | encerrar | retomar | log
---
Pedido: `$ARGUMENTS`

O motor (`python3 tools/rpg.py combat ...`) resolve; você narra. Nunca invente acerto, dano, PV ou condição: use a saída do motor. Regras que o sistema não define (cobertura, alcance, área, distância) não existem por padrão — se quiser aplicá-las, decida explicitamente com `--mod N --mod-reason "..."` e diga ao jogador que é decisão do mestre.

## iniciar
`combat start --party pc-a,pc-b --enemies "capanga x3, chefe" [--npcs npc-x:soldado] [--map map-batalha] [--set pc-a:pv=11,pvmax=11,vig=1,agi=2,def=9,det=3,per:tiro=1] [--enemy-agi N] --op <id-único>`
- Leia a ficha antes: se o motor não conseguir ler PV/atributos, ele pede `--set` — passe os valores da ficha, nunca chute.
- `--map` só lê as posições iniciais do mapa tático (o mapa do jogador não muda). Para criar o mapa, `/mapa gerar batalha`.
- Se a campanha usa `pc_rolls: jogador`, peça os dados ao jogador e passe `--roll N` (e `--damage-roll N`) nas ações dele.

## agir
`combat act <cbt> --actor <id> --action attack|move|aid|use|flee|stabilize|test|rise|deathsave --target <id> [--weapon leve|media|pesada|fogo|desarmado] [--ranged] [--determination antes|depois] --action-id <id-único>`
- Sempre um `--action-id` novo por ação (ex.: `r2-ana-1`); repetir o mesmo id não aplica duas vezes.
- Falha por 1–2: o motor deixa pendente; pergunte ao jogador "sucesso com custo ou falha?" e reenvie com `--choice custo|falha` (mesmo action-id).
- Ações do mestre: `--action condition|adjust|retire` (com motivo).
- Moral: o motor só sinaliza; você decide se os inimigos fogem/rendem.

## proximo / status / log
`combat next <cbt>` (passa o turno, expira condições, testes de ferido grave) · `combat status <cbt>` · `combat log <cbt>`.

## encerrar / retomar
- `combat end <cbt> --outcome vitoria|derrota|fuga|rendicao|outro --reason "..."` — registra o evento na linha do tempo (se houver relógio) e imprime as mudanças para aplicar na ficha dos PJs: aplique-as na ficha e confirme relendo.
- Depois do combate, se tempo relevante passou, `time advance ... --op ... --reason "combate"`.
- `combat list --active` / `combat resume [<cbt>]` para continuar um combate após fechar o terminal.
