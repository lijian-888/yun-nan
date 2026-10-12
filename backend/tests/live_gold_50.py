"""Opt-in, read-only model replay of the 50 RiceData gold questions.

Run inside the deployed API image with its real provider configuration. This
exercises the dialogue planner, governed evidence builder, response model and
numeric guards, but not HTTP authentication, SSE transport or persistence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from app.ai_gateway import prepare_egress, provider_settings
from app.dialogue_orchestration import (
    dialogue_response_guidance,
    plan_dialogue,
    validate_fact_measurements,
)
from app.main import SessionLocal, build_dialogue_evidence
from app.research_agent import stream_research_reply
from app.ricedata_fact_guard import validate_scoped_fact_answer


# These are *checks*, not generated answers. The evidence-backed expected
# meaning, uncertainty and prohibited substitutions are in the gold document.
VALUES = {
    1: ["24.1"], 2: ["114.3"], 3: ["116.8"], 4: ["77"], 5: ["86.43"],
    6: ["59.4"], 7: ["70.1"], 8: ["135.7"], 9: ["113.2"],
    10: ["15.4"], 11: ["13.8"], 12: ["17"], 13: ["15.5"],
    14: ["4"], 15: ["22.74"], 16: ["105"], 17: ["92"],
    18: ["78.7", "85.1"], 19: ["27"], 20: ["51"],
    21: ["566.3"], 22: ["561.58"], 23: ["593.6"], 24: ["584.44"],
    25: ["528.73"], 26: ["591.3"], 27: ["499.3"], 28: ["430.21"],
    29: ["486.21"], 30: ["570.98"], 38: ["24.1"], 39: ["114.3"],
    40: ["4"], 42: ["21.2"], 43: ["110"], 48: ["556.6", "568.9"],
    49: ["76.9", "78.8"], 50: ["22.2", "21.0"],
}
MISSING = {41, 45, 46, 47}
CLARIFY = set(range(31, 38)) | {44}
UNCERTAIN = {16, 17, 48}
REGIONS = {49: ["普洱", "文山"], 50: ["长江中下游", "武陵山区"]}


def _questions(document: Path) -> dict[int, list[str]]:
    cases: dict[int, list[str]] = {}
    for line in document.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\| (\d{2}) \| (.*?) \|", line)
        if not match:
            continue
        case_id = int(match.group(1))
        cell = match.group(2).strip()
        if 38 <= case_id <= 43:
            turns = re.findall(r"“([^”]+)”", cell)
            if len(turns) != 2:
                raise ValueError(f"Case {case_id} needs two turns")
        else:
            turns = [re.sub(r"\[([^]]+)]\((https?://[^)]+)\)", r"\1 \2", cell)]
        cases[case_id] = turns
    if set(cases) != set(range(1, 51)):
        raise ValueError(f"Expected 50 unique cases, got {sorted(cases)}")
    return cases


def _has_number(answer: str, value: str) -> bool:
    # Accept 77/77.0 and 21/21.0 without accepting a prefix of 770 or 21.2.
    decimal = re.escape(value.rstrip("0").rstrip(".") if "." in value else value)
    return bool(re.search(rf"(?<![\d.]){decimal}(?:\.0+)?(?![\d.])", answer))


async def _turn(provider, question: str, model_history: list[dict], trait_history: list,
                memory_state: dict | None) -> dict:
    start = time.monotonic()
    result: dict = {"question": question, "intent": None, "answer": "", "error": None,
                    "cards": [], "states": [], "guard_error": None, "elapsed_s": None}
    try:
        plan = await plan_dialogue(provider=provider, question=question, history=model_history)
        result["intent"] = plan.intent
        if plan.intent != "variety_fact":
            result["error"] = f"wrong_route:{plan.intent}"
            return result
        with SessionLocal() as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            context, cards, states = await build_dialogue_evidence(
                session, plan, question, trait_history, actor="gold-50-readonly",
                project_id="", external=provider.external, provider=provider,
                admin=False, model_history=model_history,
            )
        result["cards"] = [{"title": item.get("title"), "url": item.get("url")}
                           for item in cards]
        result["states"] = [item.get("state") for item in states]
        result["_operation_states"] = states
        safe_context = prepare_egress([context], provider=provider).texts[0]
        completion = None
        async for event in stream_research_reply(
            user_prompt=question, evidence_context=safe_context,
            memory_state=memory_state, conversation_history=model_history,
            response_guidance=dialogue_response_guidance(plan),
        ):
            if event.get("type") == "complete":
                completion = event
        if completion is None:
            raise RuntimeError("model stream ended without complete")
        answer = completion["content"]
        result["answer"] = answer
        result["_memory_state"] = completion.get("memory_state")
        try:
            validate_fact_measurements(answer, safe_context)
            validate_scoped_fact_answer(answer, next(
                (state for state in states if state.get("state") == "numeric_fact_scope"), None))
        except Exception as exc:
            result["guard_error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["elapsed_s"] = round(time.monotonic() - start, 2)
    return result


def _quick_checks(case_id: int, turns: list[dict]) -> list[str]:
    final = turns[-1]
    answer = final.get("answer") or ""
    failures = []
    for turn in turns:
        if turn.get("error"):
            failures.append(turn["error"])
        if turn.get("guard_error"):
            failures.append("guard:" + turn["guard_error"])
    if case_id in CLARIFY:
        if case_id != 44 and "research_clarification" not in final["states"]:
            failures.append("missing_clarification_state")
        if not re.search(r"请问|哪条|哪一条|哪个|确认|选择|同名", answer):
            failures.append("answer_did_not_ask")
    if case_id in MISSING and not re.search(r"未找到|未收录|未检索到|没有.{0,12}(?:数值|指标)|没有检索到|缺少.{0,12}数值|无法给出", answer):
        failures.append("missing_not_stated")
    for value in VALUES.get(case_id, []):
        if not _has_number(answer, value):
            failures.append("value_absent:" + value)
    if case_id in UNCERTAIN and not re.search(r"约|左右|待核对|候选", answer):
        failures.append("uncertainty_missing")
    for region in REGIONS.get(case_id, []):
        if region not in answer:
            failures.append("region_absent:" + region)
    required_pairs = {
        48: [("区试", "556.6"), ("生产试验", "568.9")],
        49: [("普洱", "76.9"), ("文山", "78.8")],
        50: [("长江中下游", "21"), ("武陵山区", "22.2")],
    }
    for label, value in required_pairs.get(case_id, []):
        if not re.search(re.escape(label) + r"[^\n]{0,100}" + re.escape(value) + r"(?![\d.])", answer):
            failures.append(f"pair_absent:{label}:{value}")
    if case_id == 47 and "24.1%" in answer:
        failures.append("borrowed_fujian_value")
    if case_id == 44 and re.search(r"编号分别为|编号[:：]?\s*\d{6}", answer):
        failures.append("internal_ids_exposed")
    if case_id in range(21, 31) and "公斤/亩" not in answer and "Kg/亩" not in answer:
        failures.append("yield_unit_incomplete")
    if case_id == 45 and not re.search(r"品种页面|品种资料页|不是正式审定记录", answer):
        failures.append("page_record_not_identified")
    return failures


async def _main(args) -> None:
    provider = provider_settings()
    questions = _questions(Path(args.gold))
    selected = [int(item) for item in args.ids.split(",")] if args.ids else list(range(1, 51))
    if any(item not in questions for item in selected):
        raise ValueError("Unknown case ID")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as sink:
        for case_id in selected:
            model_history: list[dict] = []
            trait_history: list = []
            memory_state = None
            turns = []
            for question in questions[case_id]:
                turn = await _turn(provider, question, model_history, trait_history, memory_state)
                memory_state = turn.pop("_memory_state", None)
                operation_states = turn.pop("_operation_states", [])
                turns.append(turn)
                model_history.extend([{"role": "user", "content": question},
                                      {"role": "assistant", "content": turn["answer"]}])
                trait_history.extend([SimpleNamespace(role="user", content=question, operation_state=[]),
                                      SimpleNamespace(role="assistant", content=turn["answer"],
                                                      operation_state=operation_states)])
                if turn["error"] or turn["guard_error"]:
                    break
            failures = _quick_checks(case_id, turns)
            record = {"id": case_id, "turns": turns, "quick_check_failures": failures,
                      "needs_human_review": True}
            sink.write(json.dumps(record, ensure_ascii=False) + "\n")
            sink.flush()
            print(f"CASE {case_id:02d} {'CHECK_FAIL' if failures else 'CHECK_OK'} "
                  f"{sum(item['elapsed_s'] for item in turns):.1f}s {','.join(failures)[:180]}", flush=True)
    print(f"RESULT_FILE {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--gold")
    parser.add_argument("--output")
    parser.add_argument("--ids", default="")
    parser.add_argument("--rescore", help="Recompute quick checks from a saved JSONL without model calls")
    arguments = parser.parse_args()
    if arguments.rescore:
        rows = [json.loads(line) for line in Path(arguments.rescore).read_text(encoding="utf-8").splitlines()]
        failures = [(row["id"], _quick_checks(row["id"], row["turns"])) for row in rows]
        print(json.dumps({"cases": len(rows), "failed": [(case_id, issues) for case_id, issues in failures if issues]},
                         ensure_ascii=False))
    else:
        if not arguments.run_live or not arguments.gold or not arguments.output:
            parser.error("Live replay requires --run-live, --gold and --output")
        asyncio.run(_main(arguments))
