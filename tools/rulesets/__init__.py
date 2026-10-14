"""Adaptadores de regras do motor de combate (tools/combat_engine.py).

O motor escolhe o adaptador pelo campo `system:` do campaign.yaml (mapa ADAPTERS abaixo).

Como criar um adaptador para outro sistema (ex.: systems/meu-sistema/system.md):
  1. Crie tools/rulesets/meu_sistema.py contendo SOMENTE regras escritas no system.md
     (cite a seção em comentário em cada regra). O que o sistema não define vai para GAPS.
  2. Registre aqui: ADAPTERS["meu-sistema"] = "meu_sistema".
  3. Interface obrigatória (veja aventura_2d6.py como referência):
     SYSTEM               id do sistema
     GAPS                 {chave: descrição} — lacunas; o motor recusa a mecânica ou exige
                          --mod N --mod-reason (decisão do mestre registrada no log)
     TEMPLATES            {modelo: {pv, defense, attack, damage, actions[, leader]}}
     WEAPONS              {classe: expressão de dano}
     CONDITIONS           {nome: descrição}
     ACTIONS              {ação: custo}  custo ∈ "acao" | "movimento" | "livre" | "mestre"
     ATTRS                {chave curta: nome do atributo}  (ex.: {"vig": "Vigor"})
     REQUIRED             chaves de ficha que um PJ precisa ter (senão o motor exige --set)
     read_sheet(texto)    -> (stats lidos com segurança, [chaves que faltaram])
     initiative(p)        -> bônus somado a INIT_DICE;  INIT_DICE (ex.: "2d6")
     init_order(ps, tot)  -> lista de ids em ordem de ação
     roll_dice(actor, target, kind, opts) -> (dados, [motivos], conflito: bool)
     attack_bonus(p, ranged, skill) -> int
     classify(margin)     -> "sucesso" | "escolha" | "falha"
     crit(natural) / complication(natural) -> bool
     damage(p, weapon, crit) -> (n_dados_faces [(n, faces)], fixo)
     at_zero(p)           aplica as consequências de 0 PV no participante
     death_save(natural)  -> "estabiliza" | "marcador"; DEATH_MARKERS
     morale(state)        -> texto do sinal de moral ou None
"""
import importlib

from sim import SimError

ADAPTERS = {"aventura-2d6": "aventura_2d6"}


def load(system):
    mod = ADAPTERS.get(system or "")
    if not mod:
        raise SimError(f"o sistema '{system}' não tem adaptador de combate. Crie tools/rulesets/<modulo>.py com a interface "
                       f"descrita em tools/rulesets/__init__.py (SYSTEM, GAPS, TEMPLATES, WEAPONS, CONDITIONS, ACTIONS, "
                       f"read_sheet, initiative, roll_dice, classify, damage, death_save, morale…) e registre-o em ADAPTERS. "
                       f"Adaptadores existentes: {', '.join(sorted(ADAPTERS))}")
    return importlib.import_module(f"rulesets.{mod}")
