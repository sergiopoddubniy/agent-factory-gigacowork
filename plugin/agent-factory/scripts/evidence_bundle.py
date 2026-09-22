#!/usr/bin/env python3
"""
evidence_bundle.py — пакет доказательств: контракт завершения работы фабрики.

Собирает в один манифест всё, что подтверждает готовность агента:
  - спецификация: id, версия, отпечаток;
  - ворота достаточности: вердикты demo и pilot;
  - структурный аудит пакета и совпадение отпечатка спецификации;
  - приёмочный прогон (если передан): PASS/FAIL по каждому случаю,
    обязательные случаи;
  - статус гипотезы результата: не проверена / подтверждена / не подтверждена;
  - допущения, с которыми собран агент.

Определение «готово» зависит от порога:
  demo  — аудит PASS, отпечаток совпадает, ворота demo PASS, в прогоне (если
          он есть) нет FAIL;
  pilot — дополнительно: ворота pilot PASS, прогон обязателен, все
          обязательные случаи PASS, гипотеза результата явно помечена
          «подтверждена» или «не подтверждена» (не «не проверена»).

Код возврата: 0 — готово по порогу, 1 — нет, 2 — ошибка входа.

    python3 evidence_bundle.py --spec AGENT_SPEC.md --package <slug> \
        [--acceptance ПРИЁМОЧНЫЙ_ПРОГОН.md] [--threshold demo|pilot] [--out <папка>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from factory_common import (  # noqa: E402
    BLOCKS, EXIT_INPUT, EXIT_OK, EXIT_RED, REQUIRED_CASES, nfc, parse_spec, read_text,
)
from check_package import audit_package  # noqa: E402
from spec_readiness import check as readiness_check  # noqa: E402

HYPOTHESIS_STATUSES = ("не проверена", "подтверждена", "не подтверждена")


def parse_acceptance(path: Path) -> dict:
    text = read_text(path, "приёмочный прогон")
    cases, current = {}, None
    for ln in text.splitlines():
        m = re.match(r"^###\s+`?([a-z0-9_]+)`?\s*$", ln.strip())
        if m:
            current = m.group(1)
            cases[current] = {"verdict": None, "reason": ""}
            continue
        if current:
            m = re.match(r"^\s*(?:\*\*)?Вердикт(?:\*\*)?\s*:\s*(PASS|FAIL|SKIP)\b", ln, re.I)
            if m:
                cases[current]["verdict"] = m.group(1).upper()
            m = re.match(r"^\s*(?:\*\*)?Причина(?:\*\*)?\s*:\s*(.*)$", ln)
            if m:
                cases[current]["reason"] = m.group(1).strip()
    hyp = None
    m = re.search(r"^\s*(?:\*\*)?Статус(?:\*\*)?\s*:\s*(не проверена|не подтверждена|подтверждена)",
                  nfc(text), re.M | re.I)
    if m:
        hyp = m.group(1).lower()
    who = re.search(r"^\s*(?:\*\*)?Кто проводил(?:\*\*)?\s*:\s*(.*)$", text, re.M)
    return {"path": str(path), "cases": cases, "hypothesis_status": hyp,
            "conducted_by": who.group(1).strip() if who else ""}


def package_fingerprint(package: Path) -> str:
    h = hashlib.sha256()
    for rel in ("skills/00-agent-skill/SKILL.md", "commands/run-agent.md"):
        p = package / rel
        if p.is_file():
            h.update(nfc(p.read_text(encoding="utf-8")).encode("utf-8"))
    return h.hexdigest()[:8]


def build_bundle(spec_path: Path, package: Path, acceptance: Path | None, threshold: str) -> dict:
    spec = parse_spec(spec_path)
    demo = readiness_check(spec, "demo")
    pilot = readiness_check(spec, "pilot")
    audit = audit_package(package, spec_path)
    acc = parse_acceptance(acceptance) if acceptance else None
    blockers = []

    if audit["status"] != "PASS":
        blockers.append("аудит пакета FAIL: " + "; ".join(audit["findings"][:3]))
    if demo["verdict"] != "PASS":
        blockers.append("ворота demo FAIL")
    if acc:
        failed = [c for c, v in acc["cases"].items() if v["verdict"] == "FAIL"]
        unverdicted = [c for c, v in acc["cases"].items() if v["verdict"] is None]
        if failed:
            blockers.append("прогон: FAIL по случаям " + ", ".join(failed))
        if unverdicted:
            blockers.append("прогон: без вердикта случаи " + ", ".join(unverdicted))
    if threshold == "pilot":
        if pilot["verdict"] != "PASS":
            blockers.append("ворота pilot FAIL")
        if not acc:
            blockers.append("для пилота приёмочный прогон обязателен")
        else:
            missing = [c for c in REQUIRED_CASES if acc["cases"].get(c, {}).get("verdict") != "PASS"]
            if missing:
                blockers.append("прогон: обязательные случаи не PASS — " + ", ".join(missing))
            if acc["hypothesis_status"] not in ("подтверждена", "не подтверждена"):
                blockers.append("гипотеза результата не проверена: работа не закрыта, пока результат "
                                "не измерен либо явно не помечен «не подтверждена»")

    assumptions = [f"блок {n}: {a}" for n in BLOCKS for a in spec.block(n).assumptions]
    return {
        "date": date.today().isoformat(),
        "threshold": threshold,
        "spec": {"id": spec.slug, "version": spec.fm("версия спецификации"), "fingerprint": spec.fp,
                 "class": spec.klass, "contour": spec.fm("контур"), "access": spec.fm("уровень доступа")},
        "package": {"path": str(package), "fingerprint": package_fingerprint(package)},
        "readiness": {"demo": demo["verdict"], "pilot": pilot["verdict"],
                      "pilot_findings": [f["code"] for f in pilot["findings"]]},
        "audit": {"status": audit["status"], "findings": audit["findings"]},
        "acceptance": acc,
        "assumptions": assumptions,
        "blockers": blockers,
        "verdict": "READY" if not blockers else "NOT READY",
    }


def render(b: dict) -> str:
    s = b["spec"]
    out = [f"# Пакет доказательств — {s['id']}", "",
           f"Дата: {b['date']} · порог: **{b['threshold']}** · вердикт: **{b['verdict']}**", "",
           "## Спецификация",
           f"- id: `{s['id']}` · версия {s['version']} · отпечаток `{s['fingerprint']}`",
           f"- класс применимости: {s['class']} · контур: {s['contour']} · доступ: {s['access']}", "",
           "## Ворота достаточности",
           f"- demo: {b['readiness']['demo']}",
           f"- pilot: {b['readiness']['pilot']}"
           + (" (" + ", ".join(b['readiness']['pilot_findings']) + ")" if b['readiness']['pilot_findings'] else ""),
           "", "## Пакет",
           f"- путь: `{b['package']['path']}` · отпечаток `{b['package']['fingerprint']}`",
           f"- аудит: {b['audit']['status']}"]
    out += [f"  - {f}" for f in b["audit"]["findings"]]
    out.append("")
    out.append("## Приёмочный прогон")
    acc = b["acceptance"]
    if not acc:
        out.append("- не проводился")
    else:
        n = len(acc["cases"])
        p = sum(1 for v in acc["cases"].values() if v["verdict"] == "PASS")
        f = sum(1 for v in acc["cases"].values() if v["verdict"] == "FAIL")
        out.append(f"- файл: `{acc['path']}` · проводил: {acc['conducted_by'] or '—'}")
        out.append(f"- случаев: {n} · PASS {p} · FAIL {f} · без вердикта {n - p - f - sum(1 for v in acc['cases'].values() if v['verdict'] == 'SKIP')}")
        for c, v in acc["cases"].items():
            tag = " (обязательный)" if c in REQUIRED_CASES else ""
            out.append(f"  - `{c}`{tag}: {v['verdict'] or 'без вердикта'}"
                       + (f" — {v['reason']}" if v["reason"] else ""))
        out.append(f"- гипотеза результата: {acc['hypothesis_status'] or 'статус не указан'}")
    out.append("")
    out.append("## Допущения, с которыми собран агент")
    out += [f"- {a}" for a in b["assumptions"]] or ["- нет"]
    out.append("")
    out.append("## Блокеры")
    out += [f"- {x}" for x in b["blockers"]] or ["- нет — готово по порогу " + b["threshold"]]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Пакет доказательств готовности агента")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--package", required=True, help="папка <agent-slug>")
    ap.add_argument("--acceptance", help="файл приёмочного прогона")
    ap.add_argument("--threshold", choices=["demo", "pilot"], default="demo")
    ap.add_argument("--out", help="папка, куда записать EVIDENCE_BUNDLE.md и .json")
    args = ap.parse_args(argv)

    acc = Path(args.acceptance) if args.acceptance else None
    if acc and not acc.is_file():
        print(f"ОШИБКА ВХОДА: файл прогона не найден: {acc}", file=sys.stderr)
        return EXIT_INPUT
    bundle = build_bundle(Path(args.spec), Path(args.package), acc, args.threshold)
    text = render(bundle)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "EVIDENCE_BUNDLE.md").write_text(text, encoding="utf-8")
        (out / "EVIDENCE_BUNDLE.json").write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"пакет доказательств: {out / 'EVIDENCE_BUNDLE.md'} · вердикт {bundle['verdict']}")
    else:
        print(text)
    return EXIT_OK if bundle["verdict"] == "READY" else EXIT_RED


if __name__ == "__main__":
    sys.exit(main())
