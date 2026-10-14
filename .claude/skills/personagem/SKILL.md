---
name: personagem
description: Criar personagem de jogador seguindo o sistema da campanha, mostrar ficha ou inventário.
argument-hint: criar | ficha [pc-id] | inventario [pc-id]
---
Subcomando: `$ARGUMENTS`

## criar
1. Leia `campaign.yaml` e a seção "Criação de personagem" do `systems/<system>/system.md`.
2. Conduza a criação passo a passo, perguntando ao jogador as escolhas (conceito, atributos, perícias, equipamento, objetivos). Ofereça sugestões, mas a decisão é do jogador. Rolagens de criação usam `python3 tools/rpg.py roll`.
3. Gere o id `pc-<nome>`; confira que `characters/pc-<nome>.md` não existe.
4. Crie a ficha a partir de `templates/entities/character.md` (com `economy: on`, a seção Dinheiro só aponta para `rpg.py res`, e o saldo inicial entra com `econ open`), com valores derivados calculados conforme o sistema (mostre o cálculo).
5. Se o histórico citar NPCs/locais novos, crie-os com ids próprios (sem segredos na ficha do PJ).
6. `python3 tools/rpg.py check` e mostre a ficha final.

## ficha [pc-id]
Mostre a ficha (padrão: único PJ ou o que estiver em cena) de forma compacta e legível. Releia o arquivo; não use memória.

## inventario [pc-id]
Mostre inventário, dinheiro e carga do PJ, e os recursos do grupo em `state/current.md`. Com `economy: on`, o dinheiro vem de `python3 tools/rpg.py res <pc-id>` (nunca da ficha).
