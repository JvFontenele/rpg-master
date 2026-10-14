"""Motor de combate modular (só stdlib). As regras vêm de um adaptador em tools/rulesets/,
escolhido pelo `system:` do campaign.yaml; o motor só cuida de turnos, persistência,
idempotência, rolagens registradas e validações.

Estado: state/sim/combat/<cbt-id>.json (gravação atômica). Cada ação tem action-id único:
reenviar o mesmo id não reaplica nada (devolve o resultado registrado ou completa a pendência).
Rolagens: rpg.roll com w.rng("<cbt>:<seed>:<action-id>:<etapa>") → reprodutíveis; cada uma vai
para state/rolls.jsonl no formato do 'rpg.py roll'. Sem relógio iniciado o combate funciona;
só o evento de fim (linha do tempo) deixa de ser gravado (o resumo fica no estado do combate).
Mapa tático: só leitura (posições iniciais dos tokens); nunca grava em maps/.
"""
import random, re

import rulesets
import sim
from rpg import camp_dir, now as real_now, read_meta, roll
from sim import SimError

OUTCOMES = ("vitoria", "derrota", "fuga", "rendicao", "outro")


# ---------------------------------------------------------------- persistência
def cdir_of(w):
    return w.dir / "state" / "sim" / "combat"


def load(w, cid):
    p = cdir_of(w) / f"{cid}.json"
    if not p.exists():
        raise SimError(f"combate '{cid}' não existe (veja 'combat list')")
    return sim.load_json(p, {})


def save(w, st):
    sim.save_json(cdir_of(w) / f"{st['id']}.json", st)


def all_combats(w):
    d = cdir_of(w)
    return [sim.load_json(p, {}) for p in sorted(d.glob("cbt-*.json"))] if d.exists() else []


def adapter(w):
    return rulesets.load(read_meta(w.dir / "campaign.yaml").get("system"))


def _player_rolls(w, p):
    return read_meta(w.dir / "campaign.yaml").get("pc_rolls", "mestre") == "jogador" and (p.get("ref") or "").startswith("pc-")


def _flush_rolls(w, rolls):
    """Grava em rolls.jsonl as rolagens ainda ausentes (o reason leva '[cbt ação]', então retomar não duplica)."""
    have = {r.get("reason") for r in sim.read_jsonl(w.dir / "state" / "rolls.jsonl")}
    for r in rolls:
        if r["reason"] not in have:
            sim.append_jsonl(w.dir / "state" / "rolls.jsonl", {k: r[k] for k in ("t", "expr", "detail", "total", "vs", "reason", "hidden")})


# ---------------------------------------------------------------- utilidades
def _expr(dice, bonus):
    return dice + (f"{bonus:+d}" if bonus else "")


def _split(expr):
    """'1d6+1+1d6' → ('1d6+1d6', 1)."""
    toks = re.findall(r"[+-]?[^+-]+", expr.replace(" ", ""))
    return "+".join(t.lstrip("+") for t in toks if "d" in t), sum(int(t) for t in toks if "d" not in t)


def _range(dice):
    lo = hi = 0
    for n, f, k in re.findall(r"(\d*)d(\d+)(?:k[hl](\d+))?", dice):
        kept = int(k or n or 1)
        lo, hi = lo + kept, hi + kept * int(f)
    return lo, hi


def _roll(w, st, act, p, dice, bonus, vs, stage, label, given=None):
    """Rola (ou usa o valor do jogador) e anexa ao registro da ação. → (natural, total)."""
    expr = _expr(dice, bonus)
    player = _player_rolls(w, p)
    if player and given is None:
        raise SimError(f"pc_rolls: jogador — peça a rolagem ({dice}) a {p['id']} e informe o resultado dos dados")
    if given is not None and not player:
        raise SimError("o resultado informado (--roll/--damage-roll) só vale para PJ com pc_rolls: jogador; aqui a ferramenta rola")
    if player:
        lo, hi = _range(dice)
        if not lo <= given <= hi:
            raise SimError(f"resultado {given} impossível para {dice} ({lo}–{hi})")
        natural, total, detail = given, given + bonus, f"informado pelo jogador: {dice}={given}" + (f" {bonus:+d}" if bonus else "")
    else:
        total, detail = roll(expr, w.rng(f"{st['id']}:{st['seed']}:{act['action_id']}:{stage}"))
        natural = total - bonus
    act["rolls"].append({"t": real_now().isoformat(), "expr": expr, "detail": detail, "total": total, "vs": vs,
                         "reason": f"[{st['id']} {act['action_id']}] {label}" + (" (rolagem do jogador)" if player else ""),
                         "hidden": bool(act.get("hidden")), "natural": natural, "fonte": "jogador" if player else "motor"})
    return natural, total


def _p(st, pid, what="participante"):
    p = next((x for x in st["participants"] if x["id"] == pid), None)
    if not p:
        raise SimError(f"{what} '{pid}' não está neste combate ({', '.join(x['id'] for x in st['participants'])})")
    return p


def _has(p, name):
    return any(c["name"] == name for c in p["conditions"])


def _conds(p):
    return ", ".join(c["name"] + (f"(até r{c['until_round']})" if c.get("until_round") else "") + (f"[fonte {c['source']}]" if c.get("source") else "")
                     for c in p["conditions"])


def _cur(st):
    return st["order"][st["turn"]]


def _coord_xy(s):
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", s.strip())
    if not m:
        raise SimError(f"coordenada inválida '{s}' (ex.: C5)")
    x = 0
    for ch in m.group(1).upper():
        x = x * 26 + ord(ch) - 64
    return x - 1, int(m.group(2)) - 1


def _decision(act, a, gap=None, R=None):
    """Modificador do mestre: exige motivo e fica registrado no log como decisão do mestre."""
    if gap and a.mod is None:
        raise SimError(f"regra não definida pelo sistema ({R.GAPS[gap]}); decisão do mestre necessária: "
                       f"informe --mod N --mod-reason \"...\"")
    if a.mod is not None or getattr(a, "dice", None) or getattr(a, "damage_mod", None):
        if not a.mod_reason:
            raise SimError("--mod/--dice/--damage-mod exigem --mod-reason (decisão do mestre)")
        act["decisions"].append({"decisao_do_mestre": a.mod_reason, "mod": a.mod or 0, "dados": getattr(a, "dice", None),
                                 "mod_dano": getattr(a, "damage_mod", None), "lacuna": gap})


# ---------------------------------------------------------------- início
def _parse_sets(items):
    out = {}
    for it in items or []:
        pid, _, kv = it.partition(":")
        if not kv:
            raise SimError(f"--set inválido '{it}' (ex.: pc-ana:pv=11,pvmax=11,vig=1,agi=2,def=9,det=3,per:tiro=1)")
        for pair in kv.split(","):
            k, _, v = pair.partition("=")
            try:
                out.setdefault(pid.strip(), {})[k.strip().lower()] = int(v)
            except ValueError:
                raise SimError(f"--set: valor não inteiro em '{pair}'")
    return out


def _apply_set(p, kv, R):
    keys = {"pv": "pv", "pvmax": "pv_max", "def": "defense", "det": "determination", "atk": "attack", "lider": "leader"}
    for k, v in kv.items():
        if k in R.ATTRS:
            p["attrs"][k] = v
        elif k.startswith("per:"):
            p["skills"][k[4:]] = v
        elif k in keys:
            p[keys[k]] = bool(v) if k == "lider" else v
        else:
            raise SimError(f"--set: chave desconhecida '{k}' (use pv, pvmax, def, det, atk, lider, {', '.join(R.ATTRS)}, per:<perícia>)")
    if "pv" in kv and "pvmax" not in kv:
        p["pv_max"] = max(p.get("pv_max") or 0, kv["pv"])


def _base(pid, ref, name, side, template=None):
    return {"id": pid, "ref": ref, "name": name, "side": side, "template": template, "pv": None, "pv_max": None,
            "defense": None, "attack": None, "damage": None, "attrs": {}, "skills": {}, "conditions": [],
            "determination": 0, "markers": 0, "out": None, "actions": 1, "used": {"acao": 0, "movimento": 0}, "pos": None}


def _from_template(p, tpl, R):
    if tpl not in R.TEMPLATES:
        raise SimError(f"modelo de inimigo desconhecido '{tpl}' (conhecidos: {', '.join(R.TEMPLATES)})")
    t = R.TEMPLATES[tpl]
    p.update(template=tpl, pv=t["pv"], pv_max=t["pv"], defense=t["defense"], attack=t["attack"], damage=t["damage"],
             actions=t["actions"], leader=bool(t.get("leader")))
    return p


def cmd_start(a):
    import maps
    w = sim.World(camp_dir(a.campaign), require_clock=False)
    R = adapter(w)
    if a.op:
        old = next((c for c in all_combats(w) if c.get("op") == a.op), None)
        if old:
            print(f"Operação '{a.op}' já criou {old['id']} — nada foi criado de novo.")
            return show(w, old)
    sets, parts = _parse_sets(a.set), []
    for pid in [x.strip() for x in (a.party or "").split(",") if x.strip()]:
        e = w.ctx.ent.get(pid)
        if not pid.startswith("pc-") or not e:
            raise SimError(f"personagem '{pid}' não existe em characters/")
        p = _base(pid, pid, e["meta"].get("name", pid), "grupo")
        stats, _ = R.read_sheet(e["text"])
        p.update({k: v for k, v in stats.items() if k not in ("attrs", "skills")})
        p["attrs"].update(stats["attrs"]), p["skills"].update(stats["skills"])
        _apply_set(p, sets.pop(pid, {}), R)
        missing = [k for k in R.REQUIRED if (p["attrs"].get(k) if k in R.ATTRS else p.get(k)) is None]
        if missing:
            raise SimError(f"não consegui ler com segurança da ficha de {pid}: {', '.join(missing)}. Informe ao iniciar: "
                           f"--set {pid}:pv=11,pvmax=11,vig=1,agi=2,def=9,det=3 (não chuto valores)")
        parts.append(p)
    for spec in [x.strip() for x in (a.npcs or "").split(",") if x.strip()]:
        nid, tpl, side = (spec.split(":") + ["", ""])[:3]
        side = side or "inimigo"
        if not nid.startswith("npc-") or nid not in w.ctx.ent:
            raise SimError(f"NPC '{nid}' não existe em npcs/")
        if side not in ("inimigo", "grupo"):
            raise SimError(f"lado inválido '{side}' em --npcs (inimigo|grupo)")
        parts.append(_from_template(_base(nid, nid, w.ctx.name(nid), side), tpl, R))
    for name in maps.parse_enemies(a.enemies):
        pid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        parts.append(_from_template(_base(pid, None, name, "inimigo"), re.sub(r"\s+\d+$", "", name).lower(), R))
    if not parts:
        raise SimError("combate sem participantes (use --party, --enemies, --npcs)")
    ids = [p["id"] for p in parts]
    if len(set(ids)) != len(ids):
        raise SimError(f"participantes repetidos: {', '.join(sorted({i for i in ids if ids.count(i) > 1}))}")
    decisions = []
    for pid, kv in sets.items():
        _apply_set(_p({"participants": parts}, pid, "--set para"), kv, R)
    for p in parts:
        if p["attrs"].get("agi") is None:
            if a.enemy_agi is None:
                raise SimError(f"regra não definida pelo sistema ({R.GAPS['agilidade_inimigos']}) — falta para {p['id']}; "
                               f"decisão do mestre necessária")
            p["attrs"]["agi"] = a.enemy_agi
    if a.enemy_agi is not None:
        decisions.append({"decisao_do_mestre": f"Agilidade {a.enemy_agi} para inimigos sem ficha", "lacuna": "agilidade_inimigos"})
    n = max([int(c["id"][4:]) for c in all_combats(w)] or [0]) + 1
    st = {"id": f"cbt-{n:04d}", "system": R.SYSTEM, "seed": a.seed if a.seed is not None else random.SystemRandom().randint(1, 2 ** 31 - 1),
          "op": a.op, "status": "ativo", "round": 1, "turn": 0, "order": [], "participants": parts, "log": [],
          "decisions": decisions, "map": None, "morale": None, "result": None, "created": real_now().isoformat(),
          "clock_at": w.now if w.clock else None}
    for p in parts:
        p["initial"] = {"pv": p["pv"], "determination": p["determination"], "conditions": [c["name"] for c in p["conditions"]]}
    # iniciativa (regra do adaptador); PJ com pc_rolls: jogador → --init pc-x=N (dados)
    given = dict((k.strip(), int(v)) for k, v in (x.split("=") for x in (a.init or "").split(",") if x.strip()))
    init = {"action_id": "iniciativa", "rolls": [], "hidden": False}
    tot = {}
    for p in parts:
        _, tot[p["id"]] = _roll(w, st, init, p, R.INIT_DICE, R.initiative(p), None, p["id"], f"iniciativa de {p['id']}", given.get(p["id"]))
    st["order"] = R.init_order(parts, tot)
    st["initiative"] = init["rolls"]
    if a.map:
        try:
            m = maps.load_map(w.dir, a.map)
        except maps.MapError as e:
            raise SimError(str(e))
        if m["type"] != "batalha":
            raise SimError(f"{a.map} não é mapa de batalha")
        st["map"] = a.map
        for p in parts:
            t = next((t for t in m["layers"].get("tokens", []) if (p["ref"] and t.get("ref") == p["ref"]) or t["name"].lower() == p["name"].lower()), None)
            if t:
                p["pos"] = {"x": t["x"], "y": t["y"], "coord": maps.coord(t["x"], t["y"]), "token": t["id"]}
    _begin_turn(st)
    save(w, st)
    _flush_rolls(w, st["initiative"])
    print(f"Combate {st['id']} iniciado (sistema {R.SYSTEM}, seed {st['seed']}).")
    for r in st["initiative"]:
        print(f"  {r['reason'].split('] ')[1]}: {r['expr']} → {r['detail']} = {r['total']}")
    sem_pos = [p["id"] for p in parts if st["map"] and not p["pos"]]
    if sem_pos:
        print(f"  aviso: sem token no mapa para {', '.join(sem_pos)} (posição desconhecida)")
    show(w, st)


# ---------------------------------------------------------------- turnos
def _begin_turn(st):
    p = _p(st, _cur(st))
    for c in p["conditions"]:
        if c["name"] == "atordoado":
            c["ativo"] = True  # §6: sem ação neste turno; some ao fim dele


def _end_turn(st, R):
    p = _p(st, _cur(st))
    p["conditions"] = [c for c in p["conditions"] if not (c["name"] == "atordoado" and c.get("ativo"))]
    p["used"] = {"acao": 0, "movimento": 0}
    for x in st["participants"]:
        x.pop("reaction", None)  # contra-ataque concedido vale só no turno em que surgiu
    notes = []
    if _has(p, "assustado"):
        notes.append(f"{p['id']} assustado: teste de Presença (8) ao fim do turno ({R.CONDITIONS['assustado']}) — act --action test --attr pre --vs 8")
    return notes


def _deathsave(w, st, R, p, given=None, act=None):
    if act is None:
        act = {"action_id": f"r{st['round']}-{p['id']}-ferido", "round": st["round"], "turn": st["turn"], "actor": p["id"],
               "kind": "deathsave", "target": None, "rolls": [], "decisions": [], "notes": [], "status": "aplicada", "result": {}}
        st["log"].append(act)
    nat, _ = _roll(w, st, act, p, "2d6", 0, 7, "ferido", f"ferido grave de {p['id']}", given)  # §10
    res = R.death_save(nat)
    if res == "estabiliza":
        p["stable"] = True
    else:
        p["markers"] += 1
        if p["markers"] >= R.DEATH_MARKERS:
            p["out"] = "morto"
    p["save_round"] = st["round"]
    st.pop("await_save", None)
    act["result"] = {"natural": nat, "resultado": res, "marcadores": p["markers"], "morto": p["out"] == "morto"}
    return act


def cmd_next(a):
    w, st, R = _open(a)
    pend = [x["action_id"] for x in st["log"] if x["status"] == "pendente"]
    if pend:
        raise SimError(f"há ação pendente ({', '.join(pend)}): resolva com act --action-id <id> --choice/--determination/--damage-roll")
    cur = _p(st, _cur(st))
    if st.get("await_save") == cur["id"]:
        raise SimError(f"{cur['id']} ainda não fez o teste de ferido grave desta rodada: act --actor {cur['id']} --action deathsave --roll N")
    notes, new = _end_turn(st, R), []
    for _ in range(len(st["order"])):
        st["turn"] += 1
        if st["turn"] == len(st["order"]):
            st["turn"], st["round"] = 0, st["round"] + 1
            for x in st["participants"]:  # condições com duração: valem até o fim da rodada indicada
                x["conditions"] = [c for c in x["conditions"] if not c.get("until_round") or c["until_round"] >= st["round"]]
        p = _p(st, _cur(st))
        if p.get("out"):
            continue
        if p["pv"] <= 0:  # §10: teste por rodada sem ajuda
            if not p.get("stable") and p.get("save_round") != st["round"]:
                if _player_rolls(w, p):
                    st["await_save"] = p["id"]
                    notes.append(f"vez de {p['id']} (0 PV): teste de ferido grave 2d6 — act --actor {p['id']} --action deathsave --roll N")
                    break
                new.append(_deathsave(w, st, R, p))
            continue
        _begin_turn(st)
        break
    else:
        notes.append("ninguém em condição de agir")
    _morale(st, R, notes)
    save(w, st)
    _flush_rolls(w, [r for x in new for r in x["rolls"]])
    for x in new:
        print(describe(x))
    for n in notes:
        print(n)
    show(w, st)


def _morale(st, R, notes):
    msg = R.morale(st)
    if msg and not st.get("morale"):
        st["morale"] = {"round": st["round"], "text": msg}
        notes.append(f"MORAL: {msg}")


# ---------------------------------------------------------------- ações
def _open(a, active=True):
    w = sim.World(camp_dir(a.campaign), require_clock=False)
    st = load(w, a.cbt)
    if active and st["status"] != "ativo":
        raise SimError(f"{st['id']} está encerrado ({st['result']['outcome']}); só consulta (status/log)")
    return w, st, adapter(w)


def cmd_act(a):
    w, st, R = _open(a)
    if a.action not in R.ACTIONS:
        raise SimError(f"ação '{a.action}' não existe em {R.SYSTEM} ({', '.join(R.ACTIONS)})")
    cost = R.ACTIONS[a.action]
    actor = a.actor or ("mestre" if cost == "mestre" else None)
    if not actor:
        raise SimError("informe --actor")
    aid = a.action_id or f"r{st['round']}-t{st['turn']}-{actor}-{a.action}"
    prev = next((x for x in st["log"] if x["action_id"] == aid), None)
    if prev:
        if (prev["actor"], prev["kind"]) != (actor, a.action):
            raise SimError(f"action-id '{aid}' já usado por {prev['actor']} ({prev['kind']})")
        if prev["status"] == "pendente" and (a.choice or a.determination == "depois" or a.damage_roll is not None):
            _resume_pending(w, st, R, prev, a)
            notes = []
            _morale(st, R, notes)
            save(w, st)
            _flush_rolls(w, prev["rolls"])
            print(describe(prev))
            for n in notes:
                print(n)
            return
        _flush_rolls(w, prev["rolls"])  # retomada após queda entre gravar estado e log de rolagens
        print(f"(ação {aid} já registrada — nada foi reaplicado; para uma 2ª ação igual no turno use outro --action-id)")
        print(describe(prev))
        return
    act = {"action_id": aid, "round": st["round"], "turn": st["turn"], "actor": actor, "kind": a.action, "target": a.target,
           "rolls": [], "decisions": [], "notes": [], "status": "aplicada", "result": {}, "hidden": a.hidden}
    tgt = _p(st, a.target, "alvo") if a.target else None
    if tgt and tgt.get("out") and a.action != "condition":
        raise SimError(f"alvo {tgt['id']} está fora de combate ({tgt['out']})")
    if cost == "mestre":
        if actor != "mestre":
            raise SimError(f"'{a.action}' é ação do mestre (--actor mestre)")
        _master(st, R, act, tgt, a)
    else:
        p = _p(st, actor, "ator")
        _validate(st, R, p, tgt, a, cost)
        _do(w, st, R, act, p, tgt, a)
        if cost in p["used"] and not act.get("reacao"):
            p["used"][cost] += 1
    st["log"].append(act)
    notes = []
    _morale(st, R, notes)
    save(w, st)
    _flush_rolls(w, act["rolls"])
    print(describe(act))
    for n in notes:
        print(n)


def _validate(st, R, p, tgt, a, cost):
    if p.get("out"):
        raise SimError(f"{p['id']} está fora de combate ({p['out']})")
    down = p["pv"] <= 0
    if a.action == "rise" or a.action == "deathsave":
        if not down:
            raise SimError(f"{p['id']} não está com 0 PV")
    elif down:
        raise SimError(f"{p['id']} está com 0 PV, caído e ferido grave: só 'rise' (Determinação) ou 'deathsave' ({R.GAPS['zero_pv_acao']})")
    if a.action == "deathsave" and _cur(st) != p["id"]:
        raise SimError(f"o teste de ferido grave é no turno de {p['id']}")
    reacao = a.action == "attack" and p.get("reaction") and tgt and p["reaction"]["against"] == tgt["id"]
    if cost in ("acao", "movimento") and _cur(st) != p["id"] and not reacao:
        raise SimError(f"não é o turno de {p['id']} (vez de {_cur(st)}); só reação explicitamente concedida (contra-ataque)")
    if cost == "acao" and not reacao:
        if any(c["name"] == "atordoado" and c.get("ativo") for c in p["conditions"]):
            raise SimError(f"{p['id']} está atordoado: sem ação neste turno (§6)")
        if p["used"]["acao"] >= p["actions"]:
            raise SimError(f"{p['id']} já usou {p['actions']} ação(ões) nesta rodada")
    if cost == "movimento" and p["used"]["movimento"] >= 1:
        raise SimError(f"{p['id']} já usou o movimento nesta rodada")
    if a.action in ("attack", "aid", "stabilize") and not tgt:
        raise SimError(f"'{a.action}' exige --target")
    if tgt and tgt["id"] == p["id"] and a.action in ("attack", "stabilize"):
        raise SimError("alvo não pode ser o próprio ator")


def _do(w, st, R, act, p, tgt, a):
    k = a.action
    if k == "attack":
        act["reacao"] = bool(p.get("reaction") and _cur(st) != p["id"])
        if act["reacao"]:
            p.pop("reaction")
        if not p.get("template") and not a.weapon:
            raise SimError(f"informe --weapon ({'|'.join(R.WEAPONS)}); {R.GAPS['armas']}")
        if not p.get("template") and not a.skill:
            act["notes"].append(f"sem --skill: só o atributo ({R.GAPS['pericia_ataque']})")
        for flag, gap in (("cover", "cobertura"), ("area", "area"), ("range", "alcance")):
            if getattr(a, flag):
                _decision(act, a, gap, R)
                break
        else:
            _decision(act, a)
        bonus = R.attack_bonus(p, a.ranged, (a.skill or "").lower())
        act["params"] = {"weapon": a.weapon, "ranged": a.ranged, "skill": a.skill, "damage_mod": a.damage_mod or 0}
        _test(w, st, R, act, p, tgt, a, bonus, tgt["defense"], f"ataque de {p['id']} em {tgt['id']}")
    elif k == "test":
        if a.attr not in R.ATTRS or a.vs is None:
            raise SimError(f"test exige --attr ({'|'.join(R.ATTRS)}) e --vs N")
        _decision(act, a)
        bonus = p["attrs"].get(a.attr, 0) + (p["skills"].get(a.skill.lower(), 0) if a.skill else 0)
        _test(w, st, R, act, p, tgt, a, bonus, a.vs, f"teste de {R.ATTRS[a.attr]} de {p['id']}")
    elif k == "stabilize":
        if tgt["pv"] > 0 or tgt.get("stable") or not _has(tgt, "ferido-grave"):
            raise SimError(f"{tgt['id']} não está morrendo (0 PV, ferido grave, não estabilizado)")
        if a.attr not in R.ATTRS:
            raise SimError(f"regra não definida pelo sistema ({R.GAPS['atributo_medicina']}); decisão do mestre necessária: --attr")
        act["decisions"].append({"decisao_do_mestre": f"Medicina testada com {R.ATTRS[a.attr]}", "lacuna": "atributo_medicina"})
        _decision(act, a)
        # §10: ajuda com Medicina (8) estabiliza
        _test(w, st, R, act, p, tgt, a, p["attrs"].get(a.attr, 0) + p["skills"].get("medicina", 0), 8, f"Medicina de {p['id']} em {tgt['id']}")
    elif k == "move":
        if _has(p, "caido"):
            raise SimError(f"{p['id']} está caído: levantar gasta o movimento (§6) — use 'stand'")
        if _has(p, "agarrado"):
            raise SimError(f"{p['id']} está agarrado: não se move (§6); teste resistido de Vigor para soltar")
        if a.to:
            x, y = _coord_xy(a.to)
            if st["map"]:
                import maps
                g = maps.load_map(w.dir, st["map"])["layers"]["grid"]
                if not (0 <= y < len(g) and 0 <= x < len(g[0])) or g[y][x] in maps.BLOCK:
                    raise SimError(f"{a.to} fora do mapa ou bloqueada")
            act["result"] = {"de": (p["pos"] or {}).get("coord"), "para": a.to.upper()}
            p["pos"] = dict(p["pos"] or {}, x=x, y=y, coord=a.to.upper())
        act["notes"].append(R.GAPS["movimento"])
    elif k == "stand":
        if not _has(p, "caido"):
            raise SimError(f"{p['id']} não está caído")
        p["conditions"] = [c for c in p["conditions"] if c["name"] != "caido"]
    elif k == "aid":
        _decision(act, a, "ajuda", R)
        tgt["bonus_next"] = tgt.get("bonus_next", 0) + a.mod
        act["result"] = {"bonus": a.mod}
    elif k == "use":
        act["notes"].append(f"{a.note or 'item'} — {R.GAPS['itens']}")
    elif k == "flee":
        p["out"] = "fugiu"
        act["notes"].append(R.GAPS["fuga"])
    elif k == "rise":  # §10: 1 Determinação a 0 PV → levanta com 1 PV, uma vez por cena
        if p.get("rose") or p["determination"] < 1:
            raise SimError(f"{p['id']} não pode: sem Determinação ou já levantou nesta cena (§10)")
        p.update(pv=1, rose=True, determination=p["determination"] - 1)
        p["conditions"] = [c for c in p["conditions"] if c["name"] != "caido"]
        act["notes"].append("ferido grave continua (§10: exige dias de repouso)")
    elif k == "deathsave":
        if p.get("stable") or p.get("save_round") == st["round"]:
            raise SimError(f"{p['id']} já estabilizou ou já testou nesta rodada")
        _deathsave(w, st, R, p, a.roll, act)


def _test(w, st, R, act, p, tgt, a, bonus, vs, label):
    if a.determination and p["determination"] < 1:
        raise SimError(f"{p['id']} não tem Determinação")
    if a.dice:
        if a.dice not in ("2d6", "3d6kh2", "3d6kl2"):
            raise SimError("--dice deve ser 2d6, 3d6kh2 ou 3d6kl2")
        dice, why = a.dice, ["dados definidos pelo mestre"]
    else:
        dice, why, conflict = R.roll_dice(p, tgt, act["kind"], {"determination": a.determination, "ranged": a.ranged, "attr": a.attr})
        if conflict:
            raise SimError(f"regra não definida pelo sistema ({R.GAPS['vantagem_e_desvantagem']}: {'; '.join(why)}); "
                           f"decisão do mestre necessária: --dice 2d6|3d6kh2|3d6kl2 --mod-reason \"...\"")
    bonus += (a.mod or 0) + p.pop("bonus_next", 0) + (a.determination == "depois")
    if a.determination:  # §4: antes = vantagem (já nos dados); depois = +1
        p["determination"] -= 1
    nat, total = _roll(w, st, act, p, dice, bonus, vs, "teste", label, a.roll)
    act["result"] = {"dados": dice, "motivos": why, "natural": nat, "total": total, "vs": vs,
                     "depois": a.determination == "depois", "complicacao": R.complication(nat)}
    _settle(w, st, R, act, p, tgt)


def _settle(w, st, R, act, p, tgt):
    r = act["result"]
    r["margem"] = r["total"] - r["vs"]
    r["resultado"] = R.classify(r["margem"])
    r["critico"] = r["resultado"] == "sucesso" and R.crit(r["natural"])
    can_depois = not r["depois"] and p["determination"] > 0 and R.classify(r["margem"] + 1) != r["resultado"]
    if r["resultado"] == "escolha" or (r["resultado"] == "falha" and can_depois):
        who = "jogador" if (p.get("ref") or "").startswith("pc-") else "mestre"
        opts = (["--choice custo (sucesso com custo; o mestre define o preço)"] if r["resultado"] == "escolha" else []) + ["--choice falha"]
        if can_depois:
            opts.append("--determination depois (+1)")
        act["status"], act["pending"] = "pendente", f"{who} escolhe: " + " | ".join(opts)
        return
    _finish(w, st, R, act, p, tgt, r["resultado"])


def _finish(w, st, R, act, p, tgt, final, dmg_roll=None):
    """final ∈ sucesso | custo | falha."""
    r = act["result"]
    r["final"] = final
    act["status"] = "aplicada"
    act.pop("pending", None)
    if final == "falha":
        return
    if act["kind"] == "attack" and "dano" not in r:
        dice, const = _split(R.damage(p, act["params"]["weapon"], r["critico"]))
        const += act["params"]["damage_mod"]
        if _player_rolls(w, p) and dmg_roll is None:
            act["status"], act["pending"] = "pendente", f"jogador rola o dano ({dice}) — act --action-id {act['action_id']} --damage-roll N"
            return
        _, tot = _roll(w, st, act, p, dice, const, None, "dano", f"dano de {p['id']} em {tgt['id']}", dmg_roll)
        r["dano"] = max(0, tot)
        r["pv"] = [tgt["pv"], max(0, tgt["pv"] - r["dano"])]
        tgt["pv"] = r["pv"][1]
        if tgt["pv"] == 0 and r["pv"][0] > 0:
            R.at_zero(tgt)
            r["caiu"] = True
    if act["kind"] == "stabilize":
        tgt["stable"] = True
    if final == "custo" and act["kind"] == "attack":  # §5: acerta, mas sofre contra-ataque ou perde posição
        tgt["reaction"] = {"against": p["id"], "from": act["action_id"]}
        act["notes"].append(f"custo: contra-ataque ({tgt['id']} pode atacar {p['id']} agora como reação) ou perda de posição — {R.GAPS['custo']}")
    elif final == "custo":
        act["notes"].append(R.GAPS["custo"])


def _resume_pending(w, st, R, act, a):
    p = _p(st, act["actor"])
    tgt = _p(st, act["target"]) if act.get("target") else None
    r = act["result"]
    if a.damage_roll is not None:
        if "dano" in r or "final" not in r:
            raise SimError("esta ação não aguarda rolagem de dano")
        return _finish(w, st, R, act, p, tgt, r["final"], a.damage_roll)
    if "final" in r:
        raise SimError(f"pendente: {act['pending']}")
    if a.determination == "depois":
        if r["depois"] or p["determination"] < 1:
            raise SimError("Determinação 'depois' indisponível (já usada ou sem pontos)")
        p["determination"] -= 1
        r["total"] += 1
        r["depois"] = True
        return _settle(w, st, R, act, p, tgt)
    if a.choice == "custo" and r["resultado"] != "escolha":
        raise SimError("'custo' só existe ao falhar por 1–2 (§4)")
    _finish(w, st, R, act, p, tgt, a.choice)


def _master(st, R, act, tgt, a):
    if not tgt:
        raise SimError(f"'{act['kind']}' exige --target")
    if act["kind"] == "condition":
        name = (a.add or a.remove or "").lower()
        if name not in R.CONDITIONS:
            raise SimError(f"condição desconhecida '{name}' ({', '.join(R.CONDITIONS)})")
        if a.add:
            if a.source:
                _p(st, a.source, "fonte")
            tgt["conditions"].append({k: v for k, v in (("name", name), ("until_round", a.until_round), ("source", a.source)) if v})
        else:
            tgt["conditions"] = [c for c in tgt["conditions"] if c["name"] != name]
        act["result"] = {"condicao": name, "op": "add" if a.add else "remove"}
    elif act["kind"] == "adjust":
        if a.pv is None or not a.mod_reason:
            raise SimError("adjust exige --pv N (pode ser negativo) e --mod-reason")
        before = tgt["pv"]
        tgt["pv"] = max(0, min(tgt["pv_max"], tgt["pv"] + a.pv))
        if tgt["pv"] == 0 and before > 0:
            R.at_zero(tgt)
        act["result"] = {"pv": [before, tgt["pv"]]}
        act["decisions"].append({"decisao_do_mestre": a.mod_reason, "pv": a.pv})
    else:  # retire
        if a.as_ not in ("fugiu", "rendido", "morto", "outro"):
            raise SimError("retire exige --as fugiu|rendido|morto|outro")
        tgt["out"] = a.as_
        act["result"] = {"saiu": a.as_}
        if a.mod_reason:
            act["decisions"].append({"decisao_do_mestre": a.mod_reason})


# ---------------------------------------------------------------- consulta / fim
def describe(x):
    r, out = x.get("result") or {}, []
    for rl in x["rolls"]:
        vs = f" vs {rl['vs']}" if rl["vs"] is not None else ""
        out.append(f"{rl['expr']} → {rl['detail']} = {rl['total']}{vs}")
    s = f"[r{x['round']}] {x['action_id']}: {x['actor']} {x['kind']}" + (f" → {x['target']}" if x.get("target") else "")
    if "resultado" in r and x["kind"] != "deathsave":
        s += f" | {r['total']} vs {r['vs']} (margem {r['margem']:+d}): {r.get('final', r['resultado']).upper()}"
        s += " CRÍTICO" if r.get("critico") else ""
        s += " — COMPLICAÇÃO (duplo 1)" if r.get("complicacao") else ""
    elif x["kind"] == "deathsave":
        s += f" | {r['resultado']} (marcadores {r['marcadores']})" + (" — MORTE" if r.get("morto") else "")
    if "dano" in r:
        s += f" | dano {r['dano']}: PV {r['pv'][0]}→{r['pv'][1]}" + (" (caído, ferido grave)" if r.get("caiu") else "")
    for k in ("pv", "saiu", "condicao", "bonus", "para"):
        if k in r and "dano" not in r:
            s += f" | {k}: {r[k]}"
    lines = [s] + [f"   {o}" for o in out] + [f"   nota: {n}" for n in x.get("notes", [])]
    lines += [f"   decisão do mestre: {d['decisao_do_mestre']}" + (f" (mod {d['mod']:+d})" if d.get("mod") else "") for d in x.get("decisions", [])]
    if x["status"] == "pendente":
        lines.append(f"   PENDENTE — {x['pending']}")
    return "\n".join(lines)


def show(w, st):
    print(f"{st['id']} [{st['status']}] rodada {st['round']} — vez de {_cur(st)}" + (f" | mapa {st['map']}" if st["map"] else ""))
    for i, pid in enumerate(st["order"]):
        p = _p(st, pid)
        tag = ">" if i == st["turn"] and st["status"] == "ativo" else " "
        extra = f" | {_conds(p)}" if p["conditions"] else ""
        extra += f" | FORA ({p['out']})" if p.get("out") else ""
        extra += f" | marcadores {p['markers']}" if p["markers"] else ""
        extra += f" | {p['pos']['coord']}" if p.get("pos") else ""
        print(f" {tag} {pid} ({p['name']}, {p['side']}) PV {p['pv']}/{p['pv_max']} Def {p['defense']} Det {p['determination']}{extra}")
    for x in st["log"]:
        if x["status"] == "pendente":
            print(f"  PENDENTE {x['action_id']}: {x['pending']}")
    if st.get("morale"):
        print(f"  MORAL (r{st['morale']['round']}): {st['morale']['text']}")
    if st["result"]:
        print(f"  resultado: {st['result']['outcome']} — {st['result']['reason']}")


def cmd_status(a):
    w, st, _ = _open(a, active=False)
    show(w, st)


def cmd_log(a):
    w, st, _ = _open(a, active=False)
    for d in st["decisions"]:
        print(f"decisão do mestre (início): {d['decisao_do_mestre']}")
    for x in st["log"]:
        print(describe(x))


def cmd_list(a):
    w = sim.World(camp_dir(a.campaign), require_clock=False)
    cs = [c for c in all_combats(w) if not a.active or c["status"] == "ativo"]
    if not cs:
        print("Nenhum combate" + (" ativo." if a.active else "."))
    for c in cs:
        res = f" — {c['result']['outcome']}" if c["result"] else f" — rodada {c['round']}, vez de {_cur(c)}"
        print(f"{c['id']} [{c['status']}]{res} ({len(c['participants'])} participantes)")


def cmd_resume(a):
    if not a.cbt:
        a.active = True
        return cmd_list(a)
    w, st, _ = _open(a, active=False)
    show(w, st)
    if st["status"] == "ativo":
        print("Retomado do estado gravado. Próximo passo: 'combat act' para quem está na vez ou 'combat next'.")


def cmd_end(a):
    w, st, R = _open(a, active=False)
    if st["status"] == "ativo":
        pend = [x["action_id"] for x in st["log"] if x["status"] == "pendente"]
        if pend:
            raise SimError(f"há ação pendente ({', '.join(pend)}); resolva antes de encerrar")
        summ = {p["id"]: {"pv": p["pv"], "pv_max": p["pv_max"], "condicoes": [c["name"] for c in p["conditions"]],
                          "marcadores": p["markers"], "caiu": p["pv"] <= 0, "fora": p.get("out"),
                          "determinacao": p["determination"]} for p in st["participants"]}
        st.update(status="encerrado", result={"outcome": a.outcome, "reason": a.reason, "round": st["round"], "summary": summ,
                                              "at": real_now().isoformat(), "event": None})
        save(w, st)
    if w.clock and not st["result"].get("event"):  # gen_key → reexecutar 'end' não duplica o evento
        ents = sorted({p["ref"] for p in st["participants"] if p.get("ref") and w.exists(p["ref"])})
        caidos = [pid for pid, s in st["result"]["summary"].items() if s["caiu"] or s["fora"]]
        st["result"]["event"] = w.record(f"Combate {st['id']}: {st['result']['outcome']} — {st['result']['reason']}"
                                         + (f" (fora de combate: {', '.join(caidos)})" if caidos else ""), "combate",
                                         entities=ents, gen_key=f"combate:{st['id']}:fim",
                                         result={"combate": st["id"], "resultado": st["result"]["outcome"], "rodadas": st["result"]["round"]})
        save(w, st)
    elif not w.clock:
        print("aviso: relógio do mundo não iniciado — evento não gravado na linha do tempo (resumo fica no estado do combate)")
    show(w, st)
    if st["result"].get("event"):
        print(f"Evento registrado: {st['result']['event']}")
    print("Mudanças a aplicar nas fichas (o motor NÃO edita fichas):")
    for p in st["participants"]:
        if not (p.get("ref") or "").startswith("pc-"):
            continue
        i, ch = p["initial"], []
        if p["pv"] != i["pv"]:
            ch.append(f"PV {i['pv']}→{p['pv']}/{p['pv_max']}")
        if p["determination"] != i["determination"]:
            ch.append(f"Determinação {i['determination']}→{p['determination']}")
        novas = [c["name"] for c in p["conditions"] if c["name"] not in i["conditions"]]
        if novas:
            ch.append("condições: " + ", ".join(novas))
        if p["markers"]:
            ch.append(f"marcadores de ferido grave: {p['markers']}")
        if p.get("out"):
            ch.append(f"saiu do combate: {p['out']}")
        print(f"  {p['id']}: " + ("; ".join(ch) or "sem mudanças"))


# ---------------------------------------------------------------- verificador
@sim.verifier
def _v_combat(w):
    P = []
    for st in all_combats(w):
        if st.get("status") != "ativo":
            continue
        ids = [x["action_id"] for x in st.get("log", [])]
        for d in sorted({i for i in ids if ids.count(i) > 1}):
            P.append(f"[combate] {st['id']}: ação duplicada no log '{d}'")
        for p in st.get("participants", []):
            if p.get("ref") and not w.exists(p["ref"]):
                P.append(f"[combate] {st['id']}: participante {p['id']} aponta para '{p['ref']}', que não existe")
            if p.get("pv") is not None and p.get("pv_max") is not None and p["pv"] > p["pv_max"]:
                P.append(f"[combate] {st['id']}: {p['id']} com PV {p['pv']} acima do máximo {p['pv_max']}")
        for pid in st.get("order", []):
            if pid not in {p["id"] for p in st.get("participants", [])}:
                P.append(f"[combate] {st['id']}: '{pid}' na ordem de iniciativa não é participante")
    return P


# ---------------------------------------------------------------- CLI
def register(sp):
    w = sim._wrap
    c0 = sp.add_parser("combat", help="motor de combate (regras pelo adaptador do sistema)").add_subparsers(dest="ccmd", required=True)
    c = c0.add_parser("start", help="inicia combate e rola iniciativa")
    c.add_argument("--party", help="pc-a,pc-b"); c.add_argument("--enemies", help='"capanga x3, chefe"')
    c.add_argument("--npcs", help="npc-x:soldado[:grupo|inimigo],..."); c.add_argument("--map", help="mapa de batalha (só leitura)")
    c.add_argument("--set", action="append", help="pc-x:pv=11,pvmax=11,vig=1,agi=2,def=9,det=3,per:tiro=1 (repetível)")
    c.add_argument("--enemy-agi", type=int, help="Agilidade dos inimigos sem ficha (decisão do mestre; lacuna do sistema)")
    c.add_argument("--init", help="pc_rolls jogador: pc-a=8,pc-b=5 (dados da iniciativa)")
    c.add_argument("--seed", type=int); c.add_argument("--op", help="id único (não cria duas vezes)"); c.add_argument("--campaign")
    c.set_defaults(f=w(cmd_start))
    c = c0.add_parser("act", help="ação de um participante (idempotente por --action-id)")
    c.add_argument("cbt"); c.add_argument("--actor"); c.add_argument("--action", required=True); c.add_argument("--target")
    c.add_argument("--weapon", choices=["leve", "media", "pesada", "fogo", "desarmado"]); c.add_argument("--ranged", action="store_true")
    c.add_argument("--skill"); c.add_argument("--attr"); c.add_argument("--vs", type=int)
    c.add_argument("--determination", choices=["antes", "depois"]); c.add_argument("--choice", choices=["custo", "falha"])
    c.add_argument("--mod", type=int); c.add_argument("--mod-reason"); c.add_argument("--dice", help="2d6|3d6kh2|3d6kl2 (mestre)")
    c.add_argument("--damage-mod", type=int); c.add_argument("--cover", action="store_true"); c.add_argument("--area", action="store_true")
    c.add_argument("--range", type=int, help="distância (lacuna: exige --mod)")
    c.add_argument("--roll", type=int, help="dados informados pelo jogador"); c.add_argument("--damage-roll", type=int)
    c.add_argument("--to", help="move: coordenada (ex.: C5)"); c.add_argument("--note")
    c.add_argument("--add"); c.add_argument("--remove"); c.add_argument("--until-round", type=int); c.add_argument("--source")
    c.add_argument("--pv", type=int); c.add_argument("--as", dest="as_"); c.add_argument("--hidden", action="store_true")
    c.add_argument("--action-id"); c.add_argument("--campaign"); c.set_defaults(f=w(cmd_act))
    for name, fn, hlp in (("status", cmd_status, "ordem, vez, PV, condições"), ("next", cmd_next, "avança turno/rodada"),
                          ("log", cmd_log, "ações registradas")):
        c = c0.add_parser(name, help=hlp); c.add_argument("cbt"); c.add_argument("--campaign"); c.set_defaults(f=w(fn))
    c = c0.add_parser("end", help="encerra, registra evento e lista mudanças para as fichas")
    c.add_argument("cbt"); c.add_argument("--outcome", required=True, choices=OUTCOMES); c.add_argument("--reason", required=True)
    c.add_argument("--campaign"); c.set_defaults(f=w(cmd_end))
    c = c0.add_parser("resume", help="lista combates ativos ou mostra o estado de um"); c.add_argument("cbt", nargs="?")
    c.add_argument("--campaign"); c.set_defaults(f=w(cmd_resume))
    c = c0.add_parser("list", help="todos os combates"); c.add_argument("--active", action="store_true"); c.add_argument("--campaign")
    c.set_defaults(f=w(cmd_list))
