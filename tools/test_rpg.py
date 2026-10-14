#!/usr/bin/env python3
"""Autoteste de tools/rpg.py em uma raiz temporária. Uso: python3 tools/test_rpg.py"""
import io, os, random, shutil, subprocess, sys, tarfile, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rpg  # noqa: E402


def protected_snapshot():
    """Hashes do que a suíte NUNCA pode alterar: campanhas reais protegidas, campanha ativa, backups e config global."""
    import hashlib
    proj, out = HERE.parent, {}
    roots = [proj / "campaigns" / "teste-validacao", proj / "campaigns" / "teste-mapas", proj / "backups"]
    files = [proj / "campaigns" / ".active"] + [Path.home() / ".claude" / f for f in
                                                ("settings.json", "settings.local.json", "CLAUDE.md", "RTK.md")]
    for r in roots:
        files += sorted(x for x in r.rglob("*") if x.is_file()) if r.exists() else []
    for f in files:
        out[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None
    return out


SNAPSHOT = protected_snapshot()

# dados
r = random.Random(1)
for expr in ["2d6+3", "1d20", "4d6kh3", "3d6kl2", "3d6!", "6d10>=8", "4dF+1", "d%", "1d8+1d6-2", "5"]:
    total, detail = rpg.roll(expr, r)
    assert isinstance(total, int) and detail, expr
for _ in range(500):
    assert 5 <= rpg.roll("2d6+3", r)[0] <= 15
    assert 3 <= rpg.roll("4d6kh3", r)[0] <= 18
    assert 0 <= rpg.roll("6d10>=8", r)[0] <= 6
    assert -4 <= rpg.roll("4dF", r)[0] <= 4
assert rpg.roll("2d6", random.Random(7)) == rpg.roll("2d6", random.Random(7))
for bad in ["", "2d", "abc", "2d6++", "0d6", "101d6", "1d1", "2d6 3"]:
    try:
        rpg.roll(bad, r)
        raise AssertionError(f"aceitou expressão inválida: {bad!r}")
    except ValueError:
        pass

# ciclo de campanha numa raiz temporária
tmp = Path(tempfile.mkdtemp())
try:
    for d in ("templates", "systems"):
        shutil.copytree(HERE.parent / d, tmp / d)
    (tmp / "campaigns").mkdir()
    env = {**os.environ, "RPG_ROOT": str(tmp)}

    def run(*args, ok=True):
        p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *args], env=env, capture_output=True, text=True)
        assert (p.returncode == 0) == ok, f"{args}: rc={p.returncode}\n{p.stdout}{p.stderr}"
        return p.stdout + p.stderr

    run("new", "teste", "--name", "Teste", "--system", "aventura-2d6")
    assert "já existe" in run("new", "teste", "--name", "X", "--system", "aventura-2d6", ok=False)
    run("new", "Ruim!", "--name", "X", "--system", "aventura-2d6", ok=False)
    run("new", "outra", "--name", "X", "--system", "inexistente", ok=False)
    c = tmp / "campaigns" / "teste"
    assert "{{" not in (c / "campaign.yaml").read_text()
    assert "OK" in run("check")
    assert "teste" in run("list")

    (c / "npcs" / "npc-a.md").write_text("---\nid: npc-b\n---\n")
    assert "difere" in run("check", ok=False)
    (c / "npcs" / "npc-a.md").write_text("---\nid: npc-a\n---\n")
    run("check")
    (c / "quests" / "quest-x.md").write_text("---\nid: quest-x\nstatus: ativa   # comentário\n---\n")
    assert rpg.read_meta(c / "quests" / "quest-x.md")["status"] == "ativa"

    run("log", "x", ok=False)  # sem sessão aberta
    run("session", "start")
    run("log", "O grupo achou a chave")
    run("log", "O traidor é o prefeito", "--hidden")
    out = run("roll", "2d6+1", "--vs", "8", "--seed", "3", "--reason", "teste")
    assert "SUCESSO" in out or "FALHA" in out
    s1 = c / "sessions" / "001.md"
    s1.write_text(s1.read_text() + "\n## Resumo\nresumo\n")
    run("log", "registro tardio")
    s = s1.read_text()
    assert s.index("registro tardio") < s.index("## Resumo"), s
    assert "achou a chave" in s and "[MESTRE] O traidor" in s
    assert '"reason": "teste"' in (c / "state" / "rolls.jsonl").read_text()
    out = run("session", "end")
    assert "sessions/001.md" in out and "Backup:" in out
    assert "status: fechada" in (c / "sessions" / "001.md").read_text()
    run("session", "start")
    assert (c / "sessions" / "002.md").exists()
    assert "ABERTA" in run("status")
    assert "Campanha ativa: teste" in run("status", "--hook")

    arc = sorted((tmp / "backups").glob("teste-*.tar.gz"))[-1]
    assert "não sobrescrevo" in run("restore", str(arc), ok=False)
    run("restore", str(arc), "--as", "teste-copia")
    assert "achou a chave" in (tmp / "campaigns" / "teste-copia" / "sessions" / "001.md").read_text()
    run("check", "--campaign", "teste-copia")

    evil = tmp / "evil.tar.gz"  # backup malicioso tentando escrever fora da pasta
    with tarfile.open(evil, "w:gz") as tf:
        data = b"x"
        ti = tarfile.TarInfo("mal/../../fora.txt"); ti.size = 1
        tf.addfile(ti, io.BytesIO(data))
    assert "recusado" in run("restore", str(evil), ok=False)
    assert not list((tmp / "campaigns").glob(".restore-*"))
    assert not (tmp.parent / "fora.txt").exists()
finally:
    shutil.rmtree(tmp)
print("OK: todos os testes passaram")

# demais suítes: mapas (Fase 2) e simulação (Fase 3)
SUITES = ["test_maps.py", "test_sim.py", "test_npc_sim.py", "test_factions.py", "test_quests.py", "test_combat.py",
          "test_integration.py", "test_life.py", "test_reliability.py",
          "test_recovery.py", "test_longrun.py", "test_agency.py", "test_audit.py"]
rc = 0
for s in SUITES:
    if (HERE / s).exists():
        r = subprocess.run([sys.executable, "-I", str(HERE / s)]).returncode
        print(f"{'OK  ' if r == 0 else 'FALHOU'} {s}")
        rc |= r
after = protected_snapshot()
changed = sorted(k for k in set(SNAPSHOT) | set(after) if SNAPSHOT.get(k) != after.get(k))
if changed:
    print("FALHOU proteção: arquivos protegidos mudaram durante a suíte:\n  " + "\n  ".join(changed))
    rc = 1
else:
    print(f"OK   proteção: {len(after)} arquivos protegidos idênticos antes e depois (teste-validacao, teste-mapas, backups, config global)")
sys.exit(rc)
