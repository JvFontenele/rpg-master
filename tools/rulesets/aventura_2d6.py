"""Adaptador de combate do sistema Aventura 2d6 (systems/aventura-2d6/system.md).

Só regras escritas no system.md (seção citada em cada uma). O que o sistema não define está
em GAPS: o motor recusa a mecânica ou exige um modificador explícito do mestre (--mod/--mod-reason).
"""
import re

SYSTEM = "aventura-2d6"
INIT_DICE = "2d6"
DEATH_MARKERS = 3  # §10: 3 marcadores = morte
ATTRS = {"vig": "Vigor", "agi": "Agilidade", "men": "Mente", "pre": "Presença"}  # §1
PHYSICAL = ("vig", "agi")  # §1: Vigor (força/luta) e Agilidade (reflexos/pontaria) — usados por "testes físicos" (§6)
REQUIRED = ("pv", "pv_max", "vig", "agi", "defense", "determination")

# §5 Inimigos. "PV 25+" do chefe → 25 por padrão; o mestre pode subir com --set chefe:pv=N.
TEMPLATES = {
    "capanga": {"pv": 4, "defense": 8, "attack": 2, "damage": "1d6", "actions": 1},
    "soldado": {"pv": 8, "defense": 9, "attack": 3, "damage": "1d6+1", "actions": 1},
    "elite": {"pv": 14, "defense": 10, "attack": 4, "damage": "1d6+2", "actions": 1},
    "chefe": {"pv": 25, "defense": 11, "attack": 5, "damage": "1d6+3", "actions": 2, "leader": True},  # §5: 2 ações/rodada
}
# §5 Dano por classe de arma
WEAPONS = {"leve": "1d6", "media": "1d6+1", "pesada": "1d6+2", "fogo": "1d6+2", "desarmado": "1d3"}
# §6 Condições (efeitos mecânicos explícitos aplicados pelo motor; o resto é lembrete)
CONDITIONS = {
    "atordoado": "sem ação no próximo turno; some ao fim dele",
    "caido": "corpo a corpo contra ele tem vantagem; levantar gasta o movimento",
    "agarrado": "não se move; teste resistido de Vigor para soltar",
    "assustado": "desvantagem contra a fonte do medo; teste de Presença (8) ao fim do turno",
    "envenenado": "desvantagem em testes físicos; perde 1 PV por cena até tratar (Medicina 8)",
    "ferido-grave": "seção 10: teste 2d6 por rodada sem ajuda",
    "exausto": "desvantagem em tudo; some após descanso completo",
}
# §5 Turno: 1 movimento + 1 ação (atacar, ajudar, usar item, conjurar, fugir)
ACTIONS = {
    "attack": "acao", "aid": "acao", "use": "acao", "flee": "acao", "stabilize": "acao",
    "move": "movimento", "stand": "movimento",  # §6 caído: levantar gasta o movimento
    "test": "livre", "rise": "livre", "deathsave": "livre",
    "condition": "mestre", "adjust": "mestre", "retire": "mestre",
}

GAPS = {
    "cobertura": "cobertura não tem efeito mecânico definido",
    "alcance": "alcance numérico de ataques à distância não é definido",
    "area": "áreas de efeito não são definidas",
    "movimento": "distância de movimento (quadrados/metros) não é definida; o motor só registra a posição",
    "armas": "armas específicas não existem; só as classes leve/média/pesada/fogo/desarmado (§5)",
    "agilidade_inimigos": "os modelos de inimigo (§5) não têm Agilidade para a iniciativa; informe --enemy-agi ou --set id:agi=N",
    "iniciativa_lado": "§5 diz 'cada lado rola'; o motor rola por participante (ambiguidade registrada)",
    "empate_iniciativa": "empate entre dois PJs ou dois não-PJs não é definido; o motor mantém a ordem de listagem",
    "vantagem_e_desvantagem": "vantagem e desvantagem ao mesmo tempo não é definido; informe --dice e --mod-reason",
    "pericia_ataque": "qual perícia vale para qual arma não é definido; informe --skill (sem ela, só o atributo, §2)",
    "atributo_medicina": "o atributo do teste de Medicina não é definido; informe --attr",
    "ajuda": "o efeito mecânico de 'ajudar' não é definido (o talento Líder dá +1, §8); informe --mod",
    "fuga": "não há teste de fuga definido; se o mestre exigir, use a ação test",
    "critico_sem_acerto": "duplo 6 que não alcança a Defesa: acerto automático não é definido (o motor exige o acerto)",
    "zero_pv_acao": "§10 não diz que 0 PV impede agir; o motor trata como incapaz até levantar (Determinação) ou ser curado",
    "lider": "quem é o líder para a moral não é definido; o motor usa o modelo chefe ou --set id:lider=1",
    "itens": "efeitos de itens (poção, medkit) não são definidos; use adjust com --mod-reason",
    "custo": "o preço do 'sucesso com custo' é escolhido pelo mestre (§4/§5); o motor só registra",
}

NUM = r"([+\-−]?\d+)(?!\s*[+\-−]\s*\d)"


def _section(text, title):
    m = re.search(rf"^##\s*{title}[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
    return m.group(1) if m else None


def _unique(pattern, text, flags=re.I):
    vals = {tuple(m.groups()) if len(m.groups()) > 1 else m.group(1) for m in re.finditer(pattern, text, flags)}
    return vals.pop() if len(vals) == 1 else None  # ausente ou ambíguo → não chuta


def _int(s):
    return int(s.replace("−", "-"))


def read_sheet(text):
    """→ (stats, faltando). Só aceita formatos inequívocos ("Agilidade +2", "PV 11/11", "Defesa 9")."""
    st, attrs = {}, {}
    sec = _section(text, "Atributos") or text
    for k, name in (("vig", "Vigor"), ("agi", "Agilidade"), ("men", "Mente"), ("pre", "Presen[çc]a")):
        v = _unique(rf"(?<!\w)\**{name}\**\s*[:=]?\s*\**\s*{NUM}", sec)
        if v is not None:
            attrs[k] = _int(v)
    st["attrs"] = attrs
    pv = _unique(r"\bPV\b\**\s*[:=]?\s*\**\s*(\d+)\s*/\s*(\d+)", text)
    if pv:
        st["pv"], st["pv_max"] = int(pv[0]), int(pv[1])
    d = _unique(rf"\bDefesa\b\**\s*[:=]?\s*\**\s*{NUM}", text)
    if d is not None:
        st["defense"] = _int(d)
    det = _unique(r"Determina[çc][ãa]o\**\s*[:=]?\s*\**\s*(\d+)", text)
    if det is not None:
        st["determination"] = int(det)
    skills = {}
    for ln in (_section(text, "Per[íi]cias") or "").splitlines():  # §2: treinado +1, especialista +2
        m = re.match(r"^[-*\s]*\**([^\n:(+\d*]+?)\**\s*[:=]?\s*(?:\(?\s*(treinad[oa]|especialista)\s*\)?|\+(\d))", ln, re.I)
        if m:
            skills[m.group(1).strip().lower()] = int(m.group(3)) if m.group(3) else (2 if m.group(2).lower() == "especialista" else 1)
    st["skills"] = skills
    missing = [k for k in REQUIRED if (attrs.get(k) if k in ATTRS else st.get(k)) is None]
    return st, missing


def initiative(p):
    return p["attrs"]["agi"]  # §5: 2d6 + Agilidade


def init_order(ps, totals):
    # §5: ordem decrescente; empate: PJ primeiro. Demais empates: ordem de listagem (GAPS["empate_iniciativa"])
    return [p["id"] for _, p in sorted(enumerate(ps), key=lambda ip: (-totals[ip[1]["id"]], not (ip[1].get("ref") or "").startswith("pc-"), ip[0]))]


def _has(p, cond, **kw):
    return any(c["name"] == cond and all(c.get(k) == v for k, v in kw.items()) for c in p["conditions"])


def roll_dice(actor, target, kind, opts):
    """→ (dados, motivos, conflito). §4: vantagem 3d6kh2 / desvantagem 3d6kl2."""
    adv, dis = [], []
    if opts.get("determination") == "antes":
        adv.append("Determinação gasta antes (§4)")
    if kind == "attack" and not opts.get("ranged") and target and _has(target, "caido"):
        adv.append("alvo caído, corpo a corpo (§6)")
    if _has(actor, "exausto"):
        dis.append("exausto (§6)")
    if _has(actor, "envenenado") and (kind == "attack" or opts.get("attr") in PHYSICAL):
        dis.append("envenenado, teste físico (§6)")
    if target and _has(actor, "assustado", source=target["id"]):
        dis.append("assustado, contra a fonte do medo (§6)")
    dice = "3d6kh2" if adv and not dis else "3d6kl2" if dis and not adv else "2d6"
    return dice, adv + dis, bool(adv and dis)


def attack_bonus(p, ranged, skill):
    if p.get("template"):
        return p["attack"]  # §5: ataque dos modelos já é o bônus total
    # §5: Vigor corpo a corpo, Agilidade à distância, + perícia
    return p["attrs"]["agi" if ranged else "vig"] + (p["skills"].get(skill, 0) if skill else 0)


def classify(margin):
    # §4: igualou/superou = sucesso; falhou por 1–2 = sucesso com custo OU falha (escolha)
    return "sucesso" if margin >= 0 else "escolha" if margin >= -2 else "falha"


def crit(natural):
    return natural == 12  # §4: duplo 6 natural (soma 12 dos 2 dados mantidos)


def complication(natural):
    return natural == 2  # §4: duplo 1 natural, mesmo se passou


def damage(p, weapon, is_crit):
    base = WEAPONS[weapon] if weapon else p.get("damage")
    return base + ("+1d6" if is_crit else "")  # §5: crítico +1d6


def at_zero(p):
    # §10: 0 PV → caído e ferido grave
    for c in ("caido", "ferido-grave"):
        if not _has(p, c):
            p["conditions"].append({"name": c})
    p.setdefault("stable", False)


def death_save(natural):
    return "estabiliza" if natural >= 7 else "marcador"  # §10: 7+ estabiliza; 6- perde 1 marcador


def morale(state):
    # §5 Moral: inimigos comuns fogem ou se rendem ao perder metade do grupo ou o líder
    en = [p for p in state["participants"] if p["side"] == "inimigo"]
    down = [p for p in en if p["pv"] <= 0 or p.get("out")]
    lead = [p for p in down if p.get("leader")]
    if not en or not (2 * len(down) >= len(en) or lead):
        return None
    comuns = [p["id"] for p in en if p not in down and not p.get("leader")]
    why = f"líder {lead[0]['id']} caiu" if lead else f"{len(down)} de {len(en)} inimigos fora de combate"
    return f"{why}; inimigos comuns restantes ({', '.join(comuns) or 'nenhum'}) fogem ou se rendem — decisão do mestre (§5)"
