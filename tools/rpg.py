#!/usr/bin/env python3
"""Ferramenta determinística do Mestre: dados, campanhas, sessões, backup.

Só stdlib. Raiz do projeto = pasta-mãe de tools/ (ou $RPG_ROOT, usado nos testes).
Uso: python3 tools/rpg.py <comando> --help
"""
import argparse, datetime as dt, hashlib, json, os, random, re, shutil, sys, tarfile
from pathlib import Path

ROOT = Path(os.environ.get("RPG_ROOT") or Path(__file__).resolve().parent.parent)
CAMPS = ROOT / "campaigns"
ACTIVE = CAMPS / ".active"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REQUIRED = ["campaign.yaml", "world.md", "timeline.md", "journal.md", "state/current.md",
            "state/knowledge.md", "secrets", "npcs", "characters", "quests", "locations", "sessions"]
ENTITY_DIRS = {"npcs": "npc-", "characters": "pc-", "quests": "quest-", "locations": "loc-"}


def die(msg):
    print(f"ERRO: {msg}", file=sys.stderr)
    sys.exit(1)


def now():
    return dt.datetime.now().astimezone().replace(microsecond=0)


def read_meta(path):
    """Chaves escalares de topo de um YAML simples ou frontmatter '---'."""
    # ponytail: parser só de 'chave: valor' no nível 0; trocar por PyYAML se o formato crescer
    meta, lines = {}, path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].strip() == "---":
        lines = lines[1:lines.index("---", 1)] if "---" in lines[1:] else []
    for ln in lines:
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*?)\s*$", ln)
        val = re.sub(r"\s+#.*$", "", m.group(2)) if m else ""  # comentário no fim da linha
        if val and not val.startswith("#"):
            meta[m.group(1)] = val.strip("\"'")
    return meta


def set_key(path, key, value):
    """Altera/insere 'chave: valor' no nível 0 de um YAML simples (gravação atômica; o resto fica intacto)."""
    t = path.read_text(encoding="utf-8")
    line = f"{key}: {value}"
    t2, n = re.subn(rf"^{re.escape(key)}:.*$", line.replace("\\", r"\\"), t, count=1, flags=re.M)
    if not n:
        t2 = t.rstrip("\n") + "\n" + line + "\n"
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(t2, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- preferências da campanha
# Ambientação = o que EXISTE no mundo. Estilos = o que recebe mais ATENÇÃO (nunca restringe acontecimentos).
# Profundidade = detalhe e simulação de rotina. Ritmo e autonomia = só narração (nenhum módulo do motor os lê).
PREFS = {
    "setting": ("moderna", "historica", "fantasia-medieval", "fantasia-urbana", "ficcao-cientifica", "cyberpunk",
                "pos-apocaliptica", "terror-sobrenatural", "realidade-alternativa", "mundo-original", "hibrida"),
    "styles": ("vida-cotidiana", "drama-familiar", "amizades", "romance", "trabalho-e-carreira", "estudos",
               "economia-pessoal", "empreendedorismo", "hobbies", "aventura", "exploracao", "investigacao", "politica",
               "faccoes", "sobrevivencia", "combate", "fantasia", "ficcao-cientifica", "terror",
               "construcao-de-comunidades", "sandbox-livre", "equilibrado"),
    "depth": ("leve", "equilibrada", "profunda"),
    "pace": ("calmo", "equilibrado", "dinamico", "intenso", "variavel"),
    "autonomy": ("baixa", "equilibrada", "alta"),
}
PREF_DEFAULTS = {"styles": "equilibrado", "depth": "equilibrada", "pace": "equilibrado", "autonomy": "equilibrada"}
SYNONYMS = {  # forma normalizada (sem acento, com hífen) → valor canônico; o mestre traduz o resto da linguagem natural
    "setting": {"historico": "historica", "medieval": "fantasia-medieval", "fantasia": "fantasia-medieval",
                "sci-fi": "ficcao-cientifica", "scifi": "ficcao-cientifica", "pos-apocalipse": "pos-apocaliptica",
                "terror": "terror-sobrenatural", "original": "mundo-original", "contemporanea": "moderna"},
    "styles": {"cotidiano": "vida-cotidiana", "familia": "drama-familiar", "amizade": "amizades", "trabalho": "trabalho-e-carreira",
               "carreira": "trabalho-e-carreira", "estudo": "estudos", "escola": "estudos", "financas": "economia-pessoal",
               "negocios": "empreendedorismo", "hobby": "hobbies", "lazer": "hobbies", "misterio": "investigacao",
               "faccao": "faccoes", "sci-fi": "ficcao-cientifica", "comunidade": "construcao-de-comunidades",
               "sandbox": "sandbox-livre", "equilibrada": "equilibrado", "balanceado": "equilibrado"},
    "depth": {"equilibrado": "equilibrada", "media": "equilibrada", "raso": "leve", "profundo": "profunda"},
    "pace": {"equilibrada": "equilibrado", "lento": "calmo", "rapido": "dinamico", "variado": "variavel"},
    "autonomy": {"equilibrado": "equilibrada", "media": "equilibrada"},
}


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s.strip().lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[\s_]+", "-", s)


def norm_pref(key, value):
    """Valida e canoniza um valor de preferência (styles: lista separada por vírgula). Erro claro se inválido."""
    items = [_norm(x) for x in str(value).split(",") if x.strip()] if key == "styles" else [_norm(value)]
    out = []
    for x in items:
        x = SYNONYMS[key].get(x, x)
        if x not in PREFS[key]:
            raise ValueError(f"{key}: '{x}' não reconhecido (opções: {', '.join(PREFS[key])})")
        if x not in out:
            out.append(x)
    if not out:
        raise ValueError(f"{key}: valor vazio")
    if key == "styles" and "equilibrado" in out and len(out) > 1:
        raise ValueError("styles: 'equilibrado' significa nenhum foco privilegiado — use sozinho ou liste os estilos")
    return ", ".join(out)


def prefs(d):
    """Preferências efetivas: campos ausentes (campanhas antigas) recebem os padrões, sem reescrever o arquivo."""
    m = read_meta(d / "campaign.yaml")
    p = {k: m.get(k) or PREF_DEFAULTS.get(k) for k in PREFS}
    p["setting"] = p["setting"] or None  # sem 'setting': a ambientação é o texto livre de 'genre'
    return p


def camp_dir(cid=None):
    cid = cid or (ACTIVE.read_text().strip() if ACTIVE.exists() else None)
    if not cid:
        die("nenhuma campanha ativa; use 'rpg.py use <id>' ou passe --campaign")
    d = CAMPS / cid
    if not (d / "campaign.yaml").exists():
        die(f"campanha '{cid}' não encontrada em {CAMPS}")
    return d


def sessions(d):
    return sorted((d / "sessions").glob("[0-9][0-9][0-9].md"))


def open_session(d):
    s = sessions(d)
    return s[-1] if s and read_meta(s[-1]).get("status") == "aberta" else None


# ---------------------------------------------------------------- dados
TERM = re.compile(r"([+-]?)\s*(?:(\d*)d(\d+|F|%)(!)?(?:(kh|kl)(\d+))?(?:(>=|>)(\d+))?|(\d+))", re.I)


def roll(expr, rng):
    """'2d6+3', '4d6kh3', '1d20+1d4-1', '3d6!', '6d10>=8' (sucessos), '4dF', 'd%'."""
    s = re.sub(r"\s*([+-])\s*", r"\1", expr.strip())  # "2d6 3" continua inválido
    if not s:
        raise ValueError("expressão vazia")
    pos, total, parts = 0, 0, []
    while pos < len(s):
        m = TERM.match(s, pos)
        if not m or m.end() == pos or (pos > 0 and not m.group(1)):
            raise ValueError(f"expressão inválida perto de '{s[pos:]}'")
        sign = -1 if m.group(1) == "-" else 1
        if m.group(9):
            val, desc = int(m.group(9)), m.group(9)
        else:
            n = int(m.group(2) or 1)
            faces = m.group(3).upper()
            if not 1 <= n <= 100:
                raise ValueError("entre 1 e 100 dados por termo")
            if faces == "F":
                rolls = [rng.choice((-1, 0, 1)) for _ in range(n)]
            else:
                f = 100 if faces == "%" else int(faces)
                if f < 2:
                    raise ValueError("dado precisa de 2+ faces")
                rolls = []
                for _ in range(n):
                    r = rng.randint(1, f)
                    rolls.append(r)
                    while m.group(4) and r == f and len(rolls) < 200:  # explode
                        r = rng.randint(1, f)
                        rolls.append(r)
            kept = rolls
            if m.group(5):
                k = int(m.group(6))
                kept = sorted(rolls, reverse=m.group(5).lower() == "kh")[:k]
            if m.group(7):
                t = int(m.group(8))
                val = sum(1 for r in kept if (r >= t if m.group(7) == ">=" else r > t))
                desc = f"{m.group(0).lstrip('+-')}={rolls}→{val} sucesso(s)"
            else:
                val = sum(kept)
                desc = f"{m.group(0).lstrip('+-')}={rolls}" + (f" mantidos {kept}" if kept is not rolls else "")
        total += sign * val
        parts.append(("-" if sign < 0 else "+") + " " + desc)
        pos = m.end()
    return total, " ".join(parts).lstrip("+ ")


def cmd_roll(a):
    rng = random.Random(a.seed) if a.seed is not None else random.SystemRandom()
    try:
        total, detail = roll(a.expr, rng)
    except ValueError as e:
        die(str(e))
    out = f"{a.expr} → {detail} = {total}"
    if a.vs is not None:
        out += f"  vs {a.vs}: {'SUCESSO' if total >= a.vs else 'FALHA'} (margem {total - a.vs:+d})"
    print(out)
    if not a.no_log:
        d = camp_dir(a.campaign) if (a.campaign or ACTIVE.exists()) else None
        if d:
            rec = {"t": now().isoformat(), "expr": a.expr, "detail": detail, "total": total,
                   "vs": a.vs, "reason": a.reason, "hidden": a.hidden}
            with open(d / "state" / "rolls.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- campanhas
def cmd_new(a):
    if not ID_RE.match(a.id):
        die("id deve ser minúsculo, com letras/números/hífen (ex.: sombras-de-varn)")
    dest = CAMPS / a.id
    if dest.exists():
        die(f"campanha '{a.id}' já existe — não sobrescrevo")
    if not (ROOT / "systems" / a.system / "system.md").exists():
        die(f"sistema '{a.system}' não existe em systems/ (ver 'rpg.py systems')")
    try:
        chosen = {k: norm_pref(k, getattr(a, k)) for k in PREFS if getattr(a, k, None)}
    except ValueError as e:
        die(str(e))
    shutil.copytree(ROOT / "templates" / "campaign", dest)
    subs = {"{{ID}}": a.id, "{{NAME}}": a.name, "{{SYSTEM}}": a.system,
            "{{GENRE}}": a.genre, "{{TONE}}": a.tone, "{{DATE}}": now().date().isoformat()}
    subs.update({f"{{{{{k.upper()}}}}}": chosen.get(k) or PREF_DEFAULTS.get(k, "") for k in PREFS})
    for p in dest.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".yaml"):
            t = p.read_text(encoding="utf-8")
            for k, v in subs.items():
                t = t.replace(k, v)
            p.write_text(t, encoding="utf-8")
    ACTIVE.write_text(a.id + "\n")
    print(f"Campanha criada: {dest.relative_to(ROOT)} (ativa)")


def cmd_list(a):
    active = ACTIVE.read_text().strip() if ACTIVE.exists() else ""
    rows = [d for d in sorted(CAMPS.iterdir()) if (d / "campaign.yaml").exists()] if CAMPS.exists() else []
    if not rows:
        print("Nenhuma campanha. Crie com /campanha nova.")
    for d in rows:
        m, ss = read_meta(d / "campaign.yaml"), sessions(d)
        last = read_meta(ss[-1]).get("data", "-") if ss else "-"
        print(f"{'*' if d.name == active else ' '} {d.name:24} {m.get('name', '?')} | sistema={m.get('system', '?')}"
              f" | sessões={len(ss)} | última={last}")


def cmd_systems(a):
    for d in sorted((ROOT / "systems").iterdir()):
        if (d / "system.md").exists():
            print(f"{d.name:20} {read_meta(d / 'system.md').get('name', '')}")


def cmd_use(a):
    camp_dir(a.id)
    ACTIVE.write_text(a.id + "\n")
    print(f"Campanha ativa: {a.id}")


def cmd_status(a):
    if a.hook and not ACTIVE.exists():
        print("[RPG] Nenhuma campanha ativa. Comandos: /campanha nova | /campanha listar | /campanha carregar <id>")
        return
    if a.hook and not (CAMPS / ACTIVE.read_text().strip() / "campaign.yaml").exists():
        print("[RPG] Campanha ativa não encontrada. Use /campanha listar.")
        return
    d = camp_dir(a.campaign)
    m, ss, op = read_meta(d / "campaign.yaml"), sessions(d), open_session(d)
    print(f"[RPG] Campanha ativa: {d.name} — {m.get('name')} (sistema: {m.get('system')}, gênero: {m.get('genre')})")
    print(f"[RPG] Sessões: {len(ss)}; " + (f"sessão ABERTA: {op.relative_to(ROOT)}" if op else "nenhuma sessão aberta"))
    print("[RPG] Para jogar: /sessao retomar (carrega estado, fichas e segredos).")
    if a.hook:
        return
    print("\n--- state/current.md ---")
    print((d / "state" / "current.md").read_text(encoding="utf-8").strip())


def cmd_style(a):
    d = camp_dir(a.campaign)
    cur, raw = prefs(d), read_meta(d / "campaign.yaml")
    if a.action == "show":
        for k in PREFS:
            v = cur[k] or f"(não definida — gênero: {raw.get('genre', '?')})"
            print(f"{k:9} {v}" + ("  (padrão)" if not raw.get(k) and k != "setting" else ""))
        return
    try:
        new = {k: norm_pref(k, getattr(a, k)) for k in PREFS if getattr(a, k) is not None}
    except ValueError as e:
        die(str(e))
    if not new:
        die("nada a alterar (use --setting/--styles/--depth/--pace/--autonomy)")
    diff = {k: v for k, v in new.items() if v != cur[k]}
    import sim
    w = sim.World(d, require_clock=False)
    last = next((e for e in reversed(w.events()) if e.get("type") == "preferencias"), None)
    para = (last.get("result") or {}).get("para") or {} if last else {}
    pending = diff and set(diff) <= set(para) and all(new.get(k) == v for k, v in para.items())
    if not diff:
        print("Preferências sem mudanças.")
        return
    if pending:  # alteração já registrada cuja gravação no campaign.yaml não terminou: só conclui
        eid = last["id"]
    else:  # evento ANTES do arquivo: uma queda entre os dois é concluída na repetição, sem evento duplicado
        res = {"de": {k: cur[k] for k in diff}, "para": diff, "motivo": a.reason}
        text = "Preferências da campanha: " + "; ".join(f"{k} {cur[k] or '—'} → {v}" for k, v in diff.items())
        if w.clock:
            eid = w.record(text, "preferencias", result=res)
        else:  # campanha sem relógio: evento no formato da Fase 2 (sem data no mundo)
            eid = w.next_id()
            w._append({"id": eid, "type": "preferencias", "text": text, "date": "(sem relógio)", "session": w.session(),
                       "result": res, "t": now().isoformat()})
    for k, v in diff.items():
        set_key(d / "campaign.yaml", k, v)
    print(f"Preferências atualizadas ({eid}): " + "; ".join(f"{k}={v}" for k, v in diff.items()))
    print("O histórico não muda: o novo foco vale daqui em diante.")


# ---------------------------------------------------------------- sessões
def cmd_session(a):
    d = camp_dir(a.campaign)
    op = open_session(d)
    if a.action == "start":
        if op:
            print(f"Sessão já aberta: {op.relative_to(ROOT)} (continuando nela)")
            return
        n = len(sessions(d)) + 1
        p = d / "sessions" / f"{n:03d}.md"
        p.write_text(f"---\nsession: {n}\nstatus: aberta\ndata: {now().date().isoformat()}\n"
                     f"inicio: {now().isoformat()}\n---\n# Sessão {n}\n\n## Eventos\n", encoding="utf-8")
        print(f"Sessão {n} aberta: {p.relative_to(ROOT)}")
    elif a.action == "end":
        if not op:
            die("nenhuma sessão aberta")
        start = dt.datetime.fromisoformat(read_meta(op)["inicio"]).timestamp()
        t = op.read_text(encoding="utf-8").replace("status: aberta", "status: fechada", 1)
        op.write_text(t + f"\n_Encerrada em {now().isoformat()}_\n", encoding="utf-8")
        print(f"Sessão encerrada: {op.relative_to(ROOT)}")
        print("Arquivos gravados desde o início da sessão:")
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.stat().st_mtime >= start:
                print(f"  {p.relative_to(d)}  {p.stat().st_size}B  sha256:{sha(p)[:12]}")
        cmd_backup(argparse.Namespace(id=d.name))


def cmd_log(a):
    d = camp_dir(a.campaign)
    op = open_session(d) or die("nenhuma sessão aberta — use 'rpg.py session start'")
    tag = "[MESTRE] " if a.hidden else ""
    line = f"- {now().strftime('%H:%M')} {tag}{a.text}\n"
    body = op.read_text(encoding="utf-8")
    cut = body.find("\n## ", body.find("## Eventos") + 1)  # mantém o log dentro de "## Eventos"
    op.write_text(body + line if cut < 0 else body[:cut].rstrip("\n") + "\n" + line + body[cut:], encoding="utf-8")
    print(f"Registrado em {op.relative_to(ROOT)}: {tag}{a.text}")


# ---------------------------------------------------------------- integridade
def cmd_check(a):
    d = camp_dir(a.campaign)
    problems, seen = [], {}
    for r in REQUIRED:
        if not (d / r).exists():
            problems.append(f"faltando: {r}")
    for sub, prefix in ENTITY_DIRS.items():
        for p in sorted((d / sub).glob("*.md")):
            eid = read_meta(p).get("id")
            if not eid:
                problems.append(f"{sub}/{p.name}: sem 'id' no frontmatter")
                continue
            if eid != p.stem:
                problems.append(f"{sub}/{p.name}: id '{eid}' difere do nome do arquivo")
            if not eid.startswith(prefix):
                problems.append(f"{sub}/{p.name}: id deveria começar com '{prefix}'")
            if eid in seen:
                problems.append(f"id duplicado '{eid}': {seen[eid]} e {sub}/{p.name}")
            seen[eid] = f"{sub}/{p.name}"
    raw = read_meta(d / "campaign.yaml")
    for k in PREFS:
        if raw.get(k):
            try:
                norm_pref(k, raw[k])
            except ValueError as e:
                problems.append(f"campaign.yaml: {e}")
    if len([s for s in sessions(d) if read_meta(s).get("status") == "aberta"]) > 1:
        problems.append("mais de uma sessão aberta")
    print("\n".join(problems) if problems else f"OK: {d.name} íntegra ({len(seen)} entidades)")
    sys.exit(1 if problems else 0)


# ---------------------------------------------------------------- backup
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cmd_backup(a):
    d = camp_dir(a.id)
    (ROOT / "backups").mkdir(exist_ok=True)
    out = ROOT / "backups" / f"{d.name}-{now().strftime('%Y%m%d-%H%M%S')}.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        tf.add(d, arcname=d.name)
    with tarfile.open(out) as tf:  # relê para confirmar que o arquivo é válido
        n = sum(1 for m in tf.getmembers() if m.isfile())
    src = sum(1 for p in d.rglob("*") if p.is_file())
    if n != src:
        die(f"backup incompleto: {n} de {src} arquivos")
    print(f"Backup: {out.relative_to(ROOT)} ({n} arquivos, sha256:{sha(out)[:12]})")


def cmd_restore(a):
    src = Path(a.archive)
    if not src.exists():
        die(f"arquivo não existe: {src}")
    with tarfile.open(src) as tf:
        tops = {m.name.split("/")[0] for m in tf.getmembers()}
        if len(tops) != 1:
            die("backup deve conter exatamente uma pasta de campanha")
        orig = tops.pop()
        target = a.as_id or orig
        if not ID_RE.match(target):
            die("id inválido")
        if (CAMPS / target).exists():
            die(f"'{target}' já existe — não sobrescrevo. Use --as <novo-id>")
        tmp = CAMPS / f".restore-{target}"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            tf.extractall(tmp, filter="data")  # bloqueia caminhos absolutos/../links
        except tarfile.TarError as e:
            shutil.rmtree(tmp, ignore_errors=True)
            die(f"backup recusado: {e}")
    (tmp / orig).rename(CAMPS / target)
    tmp.rmdir()
    if target != orig:  # a cópia passa a ter identidade própria (campaign.yaml e mapas)
        y = CAMPS / target / "campaign.yaml"
        y.write_text(re.sub(r"^id:.*$", f"id: {target}", y.read_text(encoding="utf-8"), count=1, flags=re.M), encoding="utf-8")
        for mp in sorted((CAMPS / target / "maps").glob("map-*.json")):
            m = json.loads(mp.read_text(encoding="utf-8"))
            m["campaign"] = target
            mp.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Restaurado: campaigns/{target} (de {src.name}). Ative com 'rpg.py use {target}'.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="rpg.py", description=__doc__.splitlines()[0])
    sp = p.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("roll", help="rola dados e registra em state/rolls.jsonl")
    c.add_argument("expr"); c.add_argument("--vs", type=int, help="dificuldade/alvo")
    c.add_argument("--reason", default=""); c.add_argument("--hidden", action="store_true")
    c.add_argument("--campaign"); c.add_argument("--seed", type=int); c.add_argument("--no-log", action="store_true")
    c.set_defaults(f=cmd_roll)
    c = sp.add_parser("new", help="cria campanha a partir de templates/campaign")
    c.add_argument("id"); c.add_argument("--name", required=True); c.add_argument("--system", required=True)
    c.add_argument("--genre", default="fantasia"); c.add_argument("--tone", default="aventura")
    for k, h in (("setting", "ambientação"), ("styles", "estilos de foco, por vírgula"), ("depth", "profundidade"),
                 ("pace", "ritmo"), ("autonomy", "autonomia do mundo")):
        c.add_argument(f"--{k}", help=f"{h}: {', '.join(PREFS[k])}")
    c.set_defaults(f=cmd_new)
    c = sp.add_parser("style", help="preferências da campanha (foco, profundidade, ritmo, autonomia)")
    c.add_argument("action", choices=["show", "set"])
    for k in PREFS:
        c.add_argument(f"--{k}")
    c.add_argument("--reason", default=""); c.add_argument("--campaign"); c.set_defaults(f=cmd_style)
    sp.add_parser("list", help="lista campanhas").set_defaults(f=cmd_list)
    sp.add_parser("systems", help="lista sistemas de regras").set_defaults(f=cmd_systems)
    c = sp.add_parser("use", help="define a campanha ativa"); c.add_argument("id"); c.set_defaults(f=cmd_use)
    c = sp.add_parser("status", help="resumo da campanha ativa")
    c.add_argument("--campaign"); c.add_argument("--hook", action="store_true"); c.set_defaults(f=cmd_status)
    c = sp.add_parser("session", help="abre/encerra sessão")
    c.add_argument("action", choices=["start", "end"]); c.add_argument("--campaign"); c.set_defaults(f=cmd_session)
    c = sp.add_parser("log", help="registra acontecimento na sessão aberta")
    c.add_argument("text"); c.add_argument("--hidden", action="store_true"); c.add_argument("--campaign")
    c.set_defaults(f=cmd_log)
    c = sp.add_parser("check", help="valida estrutura e ids"); c.add_argument("--campaign"); c.set_defaults(f=cmd_check)
    c = sp.add_parser("backup", help="exporta campanha para backups/")
    c.add_argument("id", nargs="?"); c.set_defaults(f=cmd_backup)
    c = sp.add_parser("restore", help="restaura backup sem sobrescrever")
    c.add_argument("archive"); c.add_argument("--as", dest="as_id"); c.set_defaults(f=cmd_restore)
    import importlib, importlib.util
    import maps, world, sim  # noqa: E402 — mapas, mundo vivo e núcleo da simulação
    maps.register(sp)
    world.register(sp)
    sim.register(sp)
    for name in sim.enabled_modules():  # módulos opcionais da simulação (cada um expõe register)
        if importlib.util.find_spec(name):
            importlib.import_module(name).register(sp)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
